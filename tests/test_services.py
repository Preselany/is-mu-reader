"""Synthetic contracts: no private recordings, accounts or network calls."""

import base64
import json
import tempfile
import threading
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from ismu import Client, ISMUError, Transport
from ismu import resources as r
from ismu.server import make_server

BASE = "https://is.muni.cz"


def response(html, path="/auth/calendar/"):
    return SimpleNamespace(text=html, url=BASE + path, headers={"Content-Type": "text/html"})


def calendar_html(events):
    value = {
        "session": {"unrelated_private_value": "never-return-this"},
        "js_init": [
            {
                "module": "RozvrhKalendar",
                "method": "init",
                "params": [events, ["8_1"], "agendaWeek", {}],
            }
        ],
    }
    return (
        '<main id="app_content"><div id="kalendar"></div></main><script>is.Design.init('
        + json.dumps(value)
        + ");</script>"
    )


def tile(mid="12", href="/auth/noticeboard/SCI/news/example/"):
    return f'<div class="dlazdice nova cervena"><div class="nazev"><a data-zprava_id="{mid}" href="{href}">Example</a></div><span class="kdy">1. 9. 2026</span><a class="kdo">Example author</a></div>'


def notice_page(page=1):
    return (
        '<main id="app_content"><div id="noticeboard">'
        + tile(str(page))
        + '<div class="pagination"><a href="/auth/noticeboard/SCI/news/">1</a><a href="/auth/noticeboard/SCI/news/stranka/2/">2</a></div></div></main>'
    )


def mailbox_html():
    return """<main id="app_content"><span id="folders">
    <input name="10_nazev" value="Inbox"><input name="10_typ" value="INBOX"><input name="10_pocty" value="1/3">
    </span><input id="currentFolder" value="10"><input id="mailCount" value="3">
    <table id="mailListTable"><tr id="mail_20" class="riadok_new chckd"><td></td>
    <td data-from_adr="sender@example.org"><span id="allFlags_20">O</span></td><td></td>
    <td>Sender</td><td><a id="read_link_20" href="?show_mail=20;folder_id=10">Příloha</a></td>
    <td>1. 9. 2026</td><td>1 KB</td></tr></table></main>"""


class ServiceContracts(unittest.TestCase):
    def test_calendar_dst_hidden_and_unknown_types(self):
        html = calendar_html(
            [
                {"typ": "8_1", "__iskup": "a", "title": "Lecture", "start": "2026-10-19T10:00:00"},
                {"typ": "8_1", "__iskup": "b", "title": "Lecture", "start": "2026-11-02T10:00:00"},
                {"typ": "future_type", "title": "New kind", "start": "2026-11-02", "allDay": 1},
            ]
        )
        data = r.calendar(html, BASE)
        self.assertTrue(data["items"][0]["start"].endswith("+02:00"))
        self.assertTrue(data["items"][1]["start"].endswith("+01:00"))
        self.assertTrue(data["items"][0]["hidden_in_ui"])
        self.assertEqual(data["items"][2]["kind"], "unknown")
        self.assertNotIn("never-return-this", json.dumps(data))

    def test_calendar_overlap_and_exclusive_end(self):
        html = calendar_html(
            [
                {
                    "typ": "6_5",
                    "title": "Overlapping",
                    "start": "2026-09-10",
                    "end": "2026-09-16",
                    "allDay": 1,
                },
                {
                    "typ": "6_5",
                    "title": "Already ended",
                    "start": "2026-09-10",
                    "end": "2026-09-15",
                    "allDay": 1,
                },
                {
                    "typ": "6_5",
                    "title": "Point",
                    "start": "2026-09-15",
                    "end": "2026-09-15",
                    "allDay": 1,
                },
                {"typ": "6_5", "title": "After range", "start": "2026-09-16", "allDay": 1},
            ]
        )
        c = Client(transport=Mock())
        c.context = {"obdobi": "1"}
        c.transport.get.return_value = response(html)
        result = c.calendar(start="2026-09-15", end="2026-09-16")
        self.assertEqual([e["title"] for e in result["items"]], ["Overlapping", "Point"])
        self.assertEqual(result["upstream_event_count"], 4)
        self.assertEqual(result["range_completeness"], "not_guaranteed")

    def test_calendar_invalid_inputs_do_not_request(self):
        c = Client(transport=Mock())
        for kw in (
            {"start": "2026-09-01"},
            {"start": "invalid", "end": "2026-10-01"},
            {"start": "2026-10-01", "end": "2026-09-01"},
            {"kinds": ["made-up"]},
        ):
            with self.subTest(kw=kw), self.assertRaises(ISMUError):
                c.calendar(**kw)
        c.transport.get.assert_not_called()

    def test_embedded_json_is_not_javascript_execution(self):
        with self.assertRaises(ISMUError):
            r.embedded_json('<script>is.Design.init(eval("malicious"));</script>', "is.Design.init")
        with self.assertRaises(ISMUError):
            r.calendar('<main id="app_content">Login</main>', BASE)

    def test_open_boxes_and_explicit_empty_state(self):
        html = '<main id="app_content"><h2>Vám otevřené odevzdávárny</h2><ul><li><a href="../dok/rfmgr?furl=%2Fel%2Fsci%2Ffall2026%2FEX101%2Fode%2Fone%2F">Homework</a></li></ul><h2>Other</h2></main>'
        item = r.open_submissions(html, BASE + "/auth/student/studijni_materialy")["items"][0]
        self.assertEqual(item["course"], "EX101")
        self.assertTrue(item["open_for_submission"])
        empty = '<main id="app_content"><h2>Vám otevřené odevzdávárny</h2><p>Žádné otevřené odevzdávárny.</p></main>'
        self.assertEqual(r.open_submissions(empty, BASE)["state"], "empty")
        with self.assertRaises(ISMUError):
            r.open_submissions(empty.replace("Žádné otevřené odevzdávárny.", "New markup"), BASE)

    def test_insert_only_box_never_claims_missing_work(self):
        path = "/el/sci/fall2026/EX101/ode/task/"
        data = {
            "uzly": {
                path: {
                    "nazev": "Task",
                    "slozka": {},
                    "w": 1,
                    "je_to_odevzdavarna": "1",
                    "prava": {"w": {"rule": {"do": "2026-10-01", "preklad": "<b>Student</b>"}}},
                }
            }
        }
        html = (
            '<main id="app_content"></main><script>is.fmgr.set(' + json.dumps(data) + ");</script>"
        )
        result = r.submission_folder(html, BASE, path)
        self.assertEqual(result["visibility"], "insert_only")
        self.assertEqual(result["submission_status"], "unknown")
        self.assertEqual(result["folder"]["permissions"]["w"]["rule"]["do"], "2026-10-01")
        self.assertEqual(result["folder"]["permissions"]["w"]["rule"]["preklad"], "Student")

    def test_submission_paths_are_checked_before_requests(self):
        c = Client(transport=Mock())
        for path in (
            "um/",
            "ode/../secret/",
            "ode/%2e%2e/",
            "ode/a/?delete=1",
            "/ode/a/",
            "ode/file.txt",
        ):
            with self.subTest(path=path), self.assertRaises(ISMUError):
                c.submission_box("EX101", path)
        c.transport.get.assert_not_called()

    def test_own_files_require_positive_uploader_evidence(self):
        path = "/el/sci/fall2026/EX101/ode/task/"
        data = {"uzly": {path: {"nazev": "Task", "slozka": {}, "r": 1}}}
        html = (
            '<main id="app_content"></main><script>is.fmgr.set('
            + json.dumps(data)
            + ');is.Design.init({"session":{"uco":"7"}});</script>'
        )
        xml = (
            "<fmgr><uzly>"
            + "".join(
                "<uzel><uzel_id>"
                + mid
                + "</uzel_id><vlozil_uco>"
                + owner
                + "</vlozil_uco><cesta>"
                + path
                + mid
                + "</cesta><objekty><objekt>"
                "<objekt_id>" + mid + "</objekt_id><jmeno_souboru>example.txt</jmeno_souboru>"
                "</objekt></objekty></uzel>"
                for mid, owner in [("20", "7"), ("21", "8")]
            )
            + "<poduzel><uzel_id>20</uzel_id><cesta>"
            + path
            + "20</cesta></poduzel></uzly></fmgr>"
        )
        c = Client(transport=Mock())
        c.course = Mock(return_value={"faculty": "sci", "semester": "fall2026", "code": "EX101"})
        c.transport.get.side_effect = [response(html, "/auth" + path), response(xml)]
        result = c.submission_box("EX101", "ode/task/")
        self.assertEqual(result["submission_status"], "own_files_visible")
        self.assertEqual([f["node_id"] for f in result["own_files"]], ["20"])
        self.assertEqual(len(result["visible_files"]), 2)

    def test_timetable_preserves_alternatives_and_exceptions(self):
        cell = '<td aria-label="St 08:00–09:50 – přednáška" class="prednaska" title="Example"><span class="predmet_kod"><a href="/predmet/sci/fall2026/EX101">EX101</a></span><span class="predmet_nazev">Example</span><span class="mistnost_oznaceni">A1</span><span class="nepravidelnost_poznamka" aria-label="Except holiday">*</span></td>'
        result = r.timetable(
            '<main id="app_content"><table><tr>'
            + cell
            + cell.replace("08:00", "10:00").replace("09:50", "11:50")
            + "</tr></table></main>",
            BASE,
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"][0]["weekday"], 3)
        self.assertEqual(result["items"][0]["exceptions_text"], "Except holiday")

    def test_exam_series_rejects_registration_links(self):
        html = '<main id="app_content">zkušební termíny<a href="/auth/student/prihl_na_zkousky?predmet=1;serie=2">Course – přihlašování k výuce</a><a href="/auth/student/prihl_na_zkousky?predmet=1;serie=2;prihlasit=1">Register</a></main>'
        result = r.exam_index(html, BASE)
        self.assertEqual(len(result["series"]), 1)
        self.assertEqual(result["series"][0]["kind"], "teaching_reservation")

    def test_exam_partial_errors_are_explicit(self):
        c = Client(transport=Mock())
        c.context = {"obdobi": "1"}
        html = '<main id="app_content">zkušební<a href="/auth/student/prihl_na_zkousky?predmet=1;serie=2">Test</a></main>'
        c.transport.get.side_effect = [response(html), ISMUError("unavailable")]
        result = c.exams()
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["errors"]), 1)

    def test_mail_paging_and_unread_do_not_use_a_guessed_flag(self):
        c = Client(transport=Mock())
        html = mailbox_html()
        row = html[html.index("<tr id=") : html.index("</tr>") + 5]
        html = html.replace(
            "</table>", row.replace("20", "21") + row.replace("20", "22") + "</table>"
        )
        c.transport.get.return_value = response(html, "/auth/mail/")
        result = c.mail_messages(folder="10", limit=1)
        self.assertTrue(result["items"][0]["unread"])
        self.assertEqual(result["next_start"], 2)
        self.assertTrue(result["pagination_incomplete"])
        self.assertEqual(result["folders"][0]["total_count"], 3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["upstream_returned_count"], 3)
        self.assertFalse(result["upstream_truncated"])
        second = c.mail_messages(folder="10", start=2, limit=1)
        self.assertEqual(second["items"][0]["id"], "21")
        self.assertFalse(
            r.mail_listing(mailbox_html().replace("riadok_new", "riadok"), BASE)["items"][0][
                "unread"
            ]
        )

    def test_mail_upstream_truncation_does_not_invent_a_next_page(self):
        c = Client(transport=Mock())
        c.transport.get.return_value = response(mailbox_html(), "/auth/mail/")
        result = c.mail_messages(folder="10", limit=50)
        self.assertTrue(result["upstream_truncated"])
        self.assertTrue(result["pagination_incomplete"])
        self.assertIsNone(result["next_start"])

    def test_mime_body_and_binary_attachment(self):
        msg = EmailMessage()
        msg["From"] = "sender@example.org"
        msg["To"] = "recipient@example.org"
        msg["Subject"] = "Příloha"
        msg["Date"] = "Wed, 16 Sep 2026 09:00:00 +0200"
        msg.set_content("Žluťoučký text")
        msg.add_alternative("<b>Žluťoučký text</b>", subtype="html")
        msg.add_attachment(
            b"\x00\xff\x01", maintype="application", subtype="octet-stream", filename="example.bin"
        )
        result, parts = r.mail_message(
            b"From sender Wed Sep 16 2026\n" + msg.as_bytes(), "20", BASE
        )
        self.assertEqual(result["headers"]["subject"], ["Příloha"])
        self.assertEqual(len(result["bodies"]), 2)
        self.assertIn("Žluťoučký", result["bodies"][0]["text"])
        self.assertEqual(parts[result["attachments"][0]["part_id"]], b"\x00\xff\x01")
        self.assertTrue(result["read_state_may_change"])
        self.assertNotIn(base64.b64encode(b"\x00\xff\x01").decode(), json.dumps(result))

    def test_invalid_mail_export_is_not_parsed_as_empty_message(self):
        with self.assertRaises(ISMUError):
            r.mail_message(b"<html>login</html>", "20", BASE)
        c = Client(transport=Mock())
        c._view_with_read_tracking = Mock(
            return_value=response("<html>login</html>", "/auth/mail/")
        )
        with self.assertRaises(ISMUError):
            c.mail_message("20", folder="10", allow_mark_read=True)

    def test_mail_body_and_download_require_explicit_opt_in(self):
        c = Client(transport=Mock())
        for value in (False, None, "false"):
            with self.assertRaises(ISMUError):
                c.mail_message("20", folder="10", allow_mark_read=value)
            with self.assertRaises(ISMUError):
                c.download_mail("20", folder="10", destination="unused.eml", allow_mark_read=value)
        c.transport.get.assert_not_called()
        c.transport._request.assert_not_called()

    def test_mail_export_allowlist_blocks_other_actions_and_duplicate_ids(self):
        url = (
            BASE
            + "/auth/mail/?handle_up=Prove%C4%8F;mail_type_up=marked;mail_handle_action_up=download;mark=20;folder_id=10"
        )
        with self.assertRaises(ISMUError):
            Transport._validate_read(url)
        for bad in (
            url.replace("download", "drop"),
            url.replace("marked", "all"),
            url + ";mark=21",
            url + ";akce=send",
            BASE + "/auth/mail/?show_mail=20",
        ):
            with self.subTest(url=bad), self.assertRaises(ISMUError):
                Transport._validate_read(bad)

    def test_notice_paging_stays_on_board_and_reports_limits(self):
        c = Client(transport=Mock())
        c.transport.get.side_effect = [
            response(notice_page(), "/auth/noticeboard/SCI/news/"),
            response(notice_page(2), "/auth/noticeboard/SCI/news/stranka/2/"),
        ]
        result = c.notices(board="SCI/news", max_pages=2)
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["pagination_incomplete"])
        self.assertTrue(result["items"][0]["unread"])
        self.assertEqual(result["items"][0]["priority"], "important")
        c.transport.get.side_effect = [response(notice_page(), "/auth/noticeboard/SCI/news/")]
        self.assertTrue(c.notices(board="SCI/news")["pagination_incomplete"])

    def test_notice_body_open_requires_explicit_read_state_consent(self):
        c = Client(transport=Mock())
        with self.assertRaises(ISMUError):
            c.notice("12")
        c.transport._request.assert_not_called()
        with self.assertRaises(ISMUError):
            c.notice("12", allow_mark_read="false")
        with self.assertRaises(ISMUError):
            r.notices(
                '<main id="app_content"><div id="noticeboard">Unexpected page</div></main>', BASE
            )
        with self.assertRaises(ISMUError):
            Transport._validate_read(BASE + "/auth/noticeboard/?operace=dej_zpravu;zprava_id=12")
        for path in ("SCI/news/message", "SCI/news?delete=1", "../secret"):
            with self.assertRaises(ISMUError):
                c.notices(board=path)

    def test_notice_detail_has_body_and_identity_check(self):
        html = '<main id="app_content"><div class="noticeboard_zprava"><h2>Example</h2><a data-id="12"></a><div class="obsah"><p>Notice body</p></div></div></main>'
        self.assertEqual(r.notice_detail(html, BASE, "12")["text"], "Notice body")
        with self.assertRaises(ISMUError):
            r.notice_detail(html, BASE, "13")

    def test_notice_redirect_validator_rejects_writes(self):
        c = Client(transport=Mock())

        def request(url, **kwargs):
            kwargs["url_validator"](BASE + "/auth/student/zapis?akce=zrus")

        c.transport._request.side_effect = request
        with self.assertRaises(ISMUError):
            c.notice("12", allow_mark_read=True)

    def test_rest_new_routes_and_strict_query_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp)
            server = make_server(client, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            headers = {"Authorization": "Bearer " + Path(tmp, "api-token").read_text()}
            cases = {
                "/v1/calendar?start=2026-09-01&end=2026-09-02": "calendar",
                "/v1/timetable": "timetable",
                "/v1/exams?max_series=2": "exams",
                "/v1/submissions": "open_submissions",
                "/v1/courses/EX101/submissions": "submissions",
                "/v1/courses/EX101/submission?path=ode%2Ftask%2F": "submission_box",
                "/v1/mail/folders": "mail_folders",
                "/v1/mail/messages?folder=10&limit=2": "mail_messages",
                "/v1/mail/messages/20?folder=10&allow_mark_read=true": "mail_message",
                "/v1/notices/12?allow_mark_read=true": "notice",
                "/v1/notices?board=SCI%2Fnews": "notices",
            }
            try:
                for path, method in cases.items():
                    with (
                        self.subTest(path=path),
                        patch.object(Client, method, return_value={"ok": True}) as mocked,
                    ):
                        res = requests.get(base + path, headers=headers, timeout=3)
                        self.assertEqual(res.status_code, 200)
                        mocked.assert_called_once()
                for path in (
                    "/v1/mail/messages/20",
                    "/v1/calendar?start=a&start=b",
                    "/v1/exams?max_series=bad",
                    "/v1/notices?allow_mark_read=1",
                    "/v1/status?unexpected=1",
                ):
                    self.assertEqual(
                        requests.get(base + path, headers=headers, timeout=3).status_code, 422
                    )
                self.assertEqual(
                    requests.get(base + "/v1/notices/12", headers=headers, timeout=3).status_code,
                    422,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
