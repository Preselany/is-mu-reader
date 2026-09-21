import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests
from test_storage import assert_private

from ismu import parsers as p
from ismu.client import Client
from ismu.server import make_server
from ismu.transport import ISMUError, Transport


class ReadContracts(unittest.TestCase):
    def test_dst_conversion(self):
        self.assertEqual(p.czech_datetime("Po 19. 10. 2026 0:00"), "2026-10-19T00:00:00+02:00")
        self.assertEqual(p.czech_datetime("Ne 1. 11. 2026 23:59"), "2026-11-01T23:59:00+01:00")

    def test_ropot_windows_and_grade_are_separate(self):
        html = """<div id="vyber_odpovedniku"><table><tbody><tr>
        <td class="uzel"><a href="?testurl=%2Fel%2Ffi%2Fpodzim2026%2FX%2Fodp%2F00.qref">Task</a></td>
        <td class="hodn-pb">0.3</td><td class="budik"><i title="Odpovědník bude otevřen."></i></td>
        <td class="poznamka"><ul><li><span class="odp_date_time">Po 19. 10. 2026 0:00</span>
        <span class="odp_date_time">Ne 1. 11. 2026 23:59</span></li></ul></td></tr></tbody></table></div>"""
        item = p.ropots(html, "https://is.muni.cz/auth/elearning/test_pruchod_el_student")["items"][
            0
        ]
        self.assertEqual(item["grading_text"], "0.3")
        self.assertTrue(item["availability"][0]["closes"].endswith("+01:00"))
        self.assertFalse(item["attempt_started_by_client"])

    def test_suppressed_list_is_not_empty(self):
        self.assertEqual(
            p.ropots('<div id="vyber_odpovedniku">kvůli jejich velkému počtu</div>', "x")["state"],
            "suppressed",
        )
        with self.assertRaises(ISMUError):
            p.ropots("<p>unexpected page</p>", "x")

    def test_direct_syllabus_link_is_discovered(self):
        d = p.course_detail(
            '<a href="/predmet/fi/podzim2026/IB015">Katalog</a><a href="/auth/el/fi/podzim2026/IB015/index.qwarp">Interaktivní osnova</a>',
            "https://is.muni.cz/auth/student/index_ajax",
        )
        self.assertTrue(d["syllabus_entry_url"].endswith("/index.qwarp"))

    def test_legitimate_syllabus_redirect_remains_read_only(self):
        Transport._validate_read(
            "https://is.muni.cz/auth/elearning/io/?so=na;qurl=%2Fel%2Ffi%2Fpodzim2026%2FIB015%2Findex.qwarp;prejit=;mode=all"
        )
        with self.assertRaises(ISMUError):
            Transport._validate_read(
                "https://is.muni.cz/auth/elearning/io/?so=na;qurl=%2Fel%2Fx.qref;mode=all"
            )

    def test_native_xml_deduplicates_and_keeps_variants(self):
        node = "<uzel_id>12</uzel_id><nazev>Notes</nazev><pocet_objektu>2</pocet_objektu><objekty><objekt><objekt_id>1</objekt_id><jmeno_souboru>a.pdf</jmeno_souboru><cesta>/el/fi/podzim2026/X/a.pdf</cesta><mime_type>application/pdf</mime_type></objekt><objekt><objekt_id>2</objekt_id><jmeno_souboru>a.txt</jmeno_souboru><cesta>/el/fi/podzim2026/X/a.txt</cesta><mime_type>text/plain</mime_type></objekt></objekty>"
        result = p.file_tree(
            "<fmgr><uzly><uzel>" + node + "</uzel><poduzel>" + node + "</poduzel></uzly></fmgr>"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["variants"]), 2)
        with self.assertRaises(ISMUError):
            p.file_tree("<html>login</html>")

    def test_locked_syllabus_section_remains_locked(self):
        result = p.syllabus(
            '<div id="io-kontejner"><div class="io-nazev-kapitoly" id="week2"><h1>Week 2</h1></div><div class="prvek-obsah-podosnova">Obsah není zveřejněný.</div></div>',
            "https://is.muni.cz/auth/el/fi/podzim2026/X/index.qwarp?mode=all",
        )
        self.assertEqual(result["sections"][0]["state"], "locked")

    def test_dangerous_get_actions_and_external_hosts_rejected(self):
        denied = [
            "https://is.muni.cz/auth/student/zapis?akce=zrus",
            "https://is.muni.cz/auth/student/prihl_na_zkousky?predmet=1;prihlasit=1",
            "https://is.muni.cz/auth/elearning/test_pruchod_el_student?testurl=x",
            "https://is.muni.cz/auth/student/index_ajax?option=delete",
            "https://is.muni.cz/auth/el/fi/podzim2026/X/odp/00.qref",
        ]
        for url in denied:
            with self.subTest(url=url), self.assertRaises(ISMUError):
                Transport._validate_read(url)
        for url in [
            "https://is.muni.cz.evil.example/auth/",
            "http://is.muni.cz/auth/",
            "https://is.muni.cz:444/auth/",
        ]:
            with self.assertRaises(ISMUError):
                Transport._validate_url(url)

    def test_path_traversal_is_rejected_before_request(self):
        c = Client(transport=Mock())
        for path in [
            "../secret",
            "%2e%2e/secret",
            "/etc/passwd",
            "x?prihlasit=1",
            "x;action=1",
            "x\\y",
        ]:
            with self.subTest(path=path), self.assertRaises(ISMUError):
                c._path("X", path)
        c.transport.get.assert_not_called()

    def test_login_submits_real_submit_button(self):
        form = '<form class="islogin_form"><input type="hidden" name="akce" value="login"><input name="credential_1" type="password"><button type="submit" name="uloz" value="uloz">Přihlásit</button></form>'

        def response(body, url):
            return SimpleNamespace(text=body, url=url, headers={"Content-Type": "text/html"})

        with tempfile.TemporaryDirectory() as tmp:
            t = Transport(tmp)
            sent = []

            def fake_request(url, **kw):
                if kw.get("method") == "POST":
                    sent.append(dict(kw["data"]))
                    return response("<html>ok</html>", "https://is.muni.cz/auth/")
                if url.endswith("/auth/"):
                    return response(form, "https://muni.islogin.cz/login/test")
                return response(
                    '<main id="app_content"></main>', "https://is.muni.cz/auth/student/predmety"
                )

            t._request = Mock(side_effect=fake_request)
            self.assertTrue(t.login("demo", "example-not-a-real-password")["authenticated"])
            self.assertEqual(sent[0]["uloz"], "uloz")
            self.assertNotIn("example-not-a-real-password", Path(tmp, "session.json").read_text())

    def test_private_session_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Transport(tmp)
            t._save()
            assert_private(self, Path(tmp, "session.json"))
            assert_private(self, Path(tmp), directory=True)

    def test_interrupted_download_becomes_reader_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(tmp, delay=0)
            response = Mock(status_code=200)
            response.iter_content.side_effect = requests.exceptions.ChunkedEncodingError(
                "private upstream body"
            )
            transport.session.request = Mock(return_value=response)
            with self.assertRaises(ISMUError) as caught:
                transport._request("https://is.muni.cz/auth/student/predmety", read_only=True)
            self.assertNotIn("private upstream body", str(caught.exception))
            response.close.assert_called_once()

    def test_snapshot_retains_partial_data_and_marks_incomplete(self):
        client = Client(transport=Mock())
        client.courses = Mock(return_value=[{"code": "EX101"}])
        client.syllabus = Mock(return_value={"state": "not_exposed", "sections": []})
        client.ropots = Mock(return_value={"state": "suppressed", "items": []})
        client.forums = Mock(return_value={"forums": [{"pagination_incomplete": True}]})
        client.reservations = Mock(side_effect=ISMUError("Temporarily unavailable"))
        client.files = Mock()
        client.notes = Mock(return_value={"text": "Example"})
        snapshot = client.snapshot(include_files=False)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(len(snapshot["errors"]), 3)
        self.assertEqual(snapshot["data"]["EX101"]["ropots"]["state"], "suppressed")
        self.assertIn("syllabus", snapshot["data"]["EX101"])
        self.assertNotIn("files", snapshot["requested_resources"])
        client.files.assert_not_called()

    def test_unknown_forum_pagination_is_reported_not_followed(self):
        client = Client(transport=Mock())
        client.course = Mock(
            return_value={
                "faculty": "fi",
                "semester": "spring2026",
                "forums": [{"forum_id": "7", "title": "Example"}],
            }
        )
        root = "https://is.muni.cz/auth/discussion/predmetove/fi/spring2026/EX101/"
        index = SimpleNamespace(
            url=root, text='<div id="discussion"><a href="prispevky/">Posts</a></div>'
        )
        page = SimpleNamespace(
            url=root + "prispevky/",
            text='<div id="discussion"><a href="?new_cursor=2">Next</a></div>',
        )
        client.transport.get = Mock(side_effect=[index, page])
        result = client.forums("EX101")
        self.assertTrue(result["forums"][0]["pagination_incomplete"])
        self.assertEqual(client.transport.get.call_count, 2)

    def test_credential_preserving_redirect_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(tmp, delay=0)
            response = Mock(
                status_code=307, headers={"Location": "https://muni.islogin.cz/login/other"}
            )
            transport.session.request = Mock(return_value=response)
            with self.assertRaises(ISMUError):
                transport._request(
                    "https://muni.islogin.cz/login/test",
                    method="POST",
                    data={"credential_1": "synthetic"},
                )
            transport.session.request.assert_called_once()

    def test_oversized_response_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(tmp, delay=0, max_bytes=4)
            response = Mock(status_code=200)
            response.iter_content.return_value = iter([b"123", b"456"])
            transport.session.request = Mock(return_value=response)
            with self.assertRaises(ISMUError):
                transport._request("https://is.muni.cz/auth/student/predmety", read_only=True)
            response.close.assert_called_once()

    def test_local_api_requires_token_and_rejects_writes_and_browser_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = SimpleNamespace(transport=SimpleNamespace(state_dir=Path(tmp)))
            server = make_server(fake, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/v1/courses"
            token = Path(tmp, "api-token").read_text()
            try:
                self.assertEqual(requests.get(url, timeout=3).status_code, 401)
                self.assertEqual(requests.post(url, timeout=3).status_code, 405)
                self.assertEqual(
                    requests.get(
                        url,
                        headers={
                            "Authorization": "Bearer " + token,
                            "Origin": "https://example.com",
                        },
                        timeout=3,
                    ).status_code,
                    403,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
