"""Consumer-facing contracts, using only invented data and local mocks."""

import json
import tempfile
import threading
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from test_ropot import ACTIVE, ENTRY, QREF, RECEIPT

from ismu import (
    AttemptUncertain,
    AuthRequired,
    Client,
    InvalidInput,
    ISMUError,
    NetworkError,
    ParseError,
    RateLimited,
    RopotSession,
    StateError,
    UnsupportedOperation,
    UpstreamError,
    models,
    parsers,
)
from ismu.errors import AuthRequired as NewAuthRequired
from ismu.server import make_server
from ismu.transport import AuthRequired as OldAuthRequired
from ismu.transport import Transport


class PublicClientContracts(unittest.TestCase):
    def test_returned_courses_and_context_cannot_mutate_routing(self):
        client = Client(transport=Mock())
        client._courses = [
            {
                "code": "EX101",
                "title": "Example",
                "course_id": 1,
                "faculty": "fi",
                "semester": "spring2026",
                "detail_params": {"predmet_id": "1"},
                "forums": [{"forum_id": "7", "title": "Forum"}],
            }
        ]
        course = client.course("EX101")
        self.assertNotIn("detail_params", course)
        course["faculty"] = "changed"
        course["forums"][0]["forum_id"] = "changed"
        listed = client.courses()
        listed[0]["forums"].clear()
        self.assertEqual(client._root("EX101"), "/el/fi/spring2026/EX101/")
        self.assertEqual(client.course("EX101")["forums"][0]["forum_id"], "7")
        client.transport.get.return_value = SimpleNamespace(text="unused")
        with patch("ismu.client.p.course_index", return_value=({"studium": "3"}, [])):
            status = client.status()
        status["context"]["studium"] = "changed"
        self.assertEqual(client.context["studium"], "3")

    def test_public_login_invalidates_cache_and_keeps_transport_compatibility(self):
        client = Client(transport=Mock())
        client._courses = [{"code": "OLD"}]
        result = {"authenticated": True, "already_authenticated": False, "password_saved": False}
        client.transport.login.return_value = result
        self.assertEqual(client.login("example", "synthetic-password"), result)
        client.transport.login.assert_called_once_with("example", "synthetic-password")
        self.assertIsNone(client._courses)
        self.assertNotIn("password", vars(client))
        client._courses = [{"code": "OLD"}]
        client.transport.login.side_effect = AuthRequired("Example challenge")
        with self.assertRaises(AuthRequired):
            client.login("example", "synthetic-password")
        self.assertIsNone(client._courses)
        with self.assertRaises(InvalidInput):
            client.login("", "synthetic-password")

    def test_ropot_factory_does_not_inspect_or_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(tmp)
            with patch("ismu.ropot.RopotTransport.exchange") as exchange:
                attempt = client.ropot({"qref_path": QREF}, allow_writes=True)
                self.assertEqual(attempt.qref_path, QREF)
                self.assertTrue(attempt.allow_writes)
                self.assertEqual(attempt.transport.state_dir, Path(tmp))
                self.assertFalse(client.ropot(QREF).allow_writes)
                exchange.assert_not_called()
            with self.assertRaises(InvalidInput):
                client.ropot({"qref_path": None})

    def test_typed_dicts_are_still_plain_json(self):
        result = models.Notes(source_url="https://is.muni.cz/example", text="Example")
        self.assertIs(type(result), dict)
        self.assertEqual(json.loads(json.dumps(result)), result)


class QuestionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.attempt = RopotSession(self.tmp.name, QREF, public=True, allow_writes=True)
        self.attempt._store(ACTIVE)
        self.attempt.transport.exchange = Mock()

    def test_text_and_checkbox_answers_need_no_native_names(self):
        q = self.attempt.question(1)
        self.assertIn("f(x)=x^5", q.text)
        choices = self.attempt.question("2").fields[0].choices
        self.assertEqual([(c.value, c.label) for c in choices], [("A", "A"), ("B", "B")])
        answers = [q.answer("5*x^4"), self.attempt.question(2).answer([choices[1].value])]
        self.attempt.transport.exchange.assert_not_called()
        saved = ACTIVE.replace('value="" maxlength', 'value="5*x^4" maxlength')
        saved = saved.replace('value="A" checked', 'value="A"').replace(
            'value="B">', 'value="B" checked>'
        )
        self.attempt.transport.exchange.return_value = SimpleNamespace(
            text=saved, url=self.attempt.entry_url
        )
        result = self.attempt.save(answers)
        self.assertEqual(result["action"], "save")
        payload = self.attempt.transport.exchange.call_args.kwargs["data"]
        self.assertIn(("tst_1_l_a_1", "5*x^4"), payload)
        self.assertIn(("tst_2_c", "B"), payload)
        self.assertEqual([v for k, v in payload if k == "test_sklad"], ["tst_1", "tst_2"])
        with self.assertRaises(StateError):
            self.attempt.save(answers)
        self.assertEqual(self.attempt.transport.exchange.call_count, 1)

    def test_single_answer_submit_and_existing_mapping_both_work(self):
        answer = self.attempt.question(1).answer("5*x^4")
        self.attempt.transport.exchange.return_value = SimpleNamespace(
            text=RECEIPT, url=self.attempt.entry_url
        )
        with self.assertRaises(UnsupportedOperation):
            self.attempt.submit(answer)
        self.attempt.transport.exchange.assert_not_called()
        self.assertTrue(self.attempt.submit(answer, confirm=True)["submission_confirmed"])
        self.attempt._store(ACTIVE)
        self.assertTrue(
            self.attempt.submit({"tst_1_l_a_1": "5*x^4"}, confirm=True)["submission_confirmed"]
        )

    def test_multiple_blanks_require_explicit_field_choice(self):
        html = ACTIVE.replace(
            'maxlength="30">', 'maxlength="30"><input name="tst_second_blank" value="">'
        )
        self.attempt._store(html)
        question = self.attempt.question(1)
        with self.assertRaises(InvalidInput):
            question.answer("value")
        answers = [question.fields[0].answer("first"), question.fields[1].answer("second")]
        self.attempt.transport.exchange.return_value = SimpleNamespace(
            text=RECEIPT, url=self.attempt.entry_url
        )
        self.attempt.submit(answers, confirm=True)
        payload = self.attempt.transport.exchange.call_args.kwargs["data"]
        self.assertIn(("tst_1_l_a_1", "first"), payload)
        self.assertIn(("tst_second_blank", "second"), payload)

    def test_wrong_options_duplicate_answers_and_stale_capture_never_write(self):
        with self.assertRaises(InvalidInput):
            self.attempt.question(2).answer(["not-an-option"])
        with self.assertRaises(InvalidInput):
            self.attempt.question(1).answer("x" * 31)
        with self.assertRaises(InvalidInput):
            self.attempt.question(99)
        answer = self.attempt.question(1).answer("5*x^4")
        with self.assertRaises(InvalidInput):
            self.attempt.save([answer, answer])
        # Identical HTML in a new capture is still a new state transition.
        self.attempt._store(ACTIVE)
        with self.assertRaises(StateError):
            self.attempt.save(answer)
        self.attempt.transport.exchange.assert_not_called()

    def test_answers_are_account_bound_and_do_not_hold_mutable_lists(self):
        selection = ["B"]
        answer = self.attempt.question(2).answer(selection)
        selection.append("A")
        with tempfile.TemporaryDirectory() as other:
            attempt = RopotSession(other, QREF, public=True, allow_writes=True)
            # Even a copied capture cannot transfer answers to a different state directory.
            attempt.cache_path.parent.mkdir(parents=True)
            attempt.cache_path.write_bytes(self.attempt.cache_path.read_bytes())
            attempt.cache_path.chmod(0o600)
            attempt.transport.exchange = Mock()
            with self.assertRaises(StateError):
                attempt.save(answer)
            attempt.transport.exchange.assert_not_called()
        self.attempt.transport.exchange.return_value = SimpleNamespace(
            text=RECEIPT, url=self.attempt.entry_url
        )
        self.attempt.submit(answer, confirm=True)
        payload = self.attempt.transport.exchange.call_args.kwargs["data"]
        self.assertEqual([v for k, v in payload if k == "tst_2_c"], ["B"])

    def test_pending_write_preserves_uncertainty_for_new_helpers(self):
        answer = self.attempt.question(1).answer("5*x^4")
        self.attempt.transport.exchange.side_effect = NetworkError("Disconnected")
        with self.assertRaises(AttemptUncertain):
            self.attempt.save(answer)
        with self.assertRaises(AttemptUncertain):
            self.attempt.question_list()
        with self.assertRaises(AttemptUncertain):
            self.attempt.save(answer)
        self.assertEqual(self.attempt.transport.exchange.call_count, 1)

    def test_not_started_and_default_disabled_writes_remain_explicit(self):
        self.attempt._store(ENTRY)
        with self.assertRaises(StateError):
            self.attempt.question_list()
        self.attempt._store(ACTIVE)
        self.attempt.allow_writes = False
        with self.assertRaises(UnsupportedOperation):
            self.attempt.save(self.attempt.question(1).answer("5*x^4"))
        self.attempt.transport.exchange.assert_not_called()

    def test_bad_local_cache_is_state_error_and_never_refetched(self):
        for content in ["{broken", "[]", '{"html": 123}', '{"html": "", "pending": "unknown"}']:
            self.attempt.cache_path.write_text(content)
            with self.subTest(content=content), self.assertRaises(StateError):
                self.attempt.question_list()
        self.attempt.transport.exchange.assert_not_called()

    def test_changed_numeric_form_metadata_is_a_parser_error(self):
        for html in [
            ACTIVE.replace('maxlength="30"', 'maxlength="unknown"'),
            ACTIVE.replace('name="celkem_stran" value="1"', 'name="celkem_stran" value="unknown"'),
        ]:
            self.attempt._store(html)
            with self.assertRaises(ParseError):
                self.attempt.question_list()
        self.attempt.transport.exchange.assert_not_called()

    def test_controls_without_explicit_values_use_native_on_default(self):
        self.attempt._store(ACTIVE.replace('value="A"', ""))
        field = self.attempt.question(2).fields[0]
        self.assertEqual(field.choices[0].value, "on")
        field.answer(["on"])


class ErrorContracts(unittest.TestCase):
    def test_bad_session_files_fail_cleanly_without_partial_cookies_or_private_tracebacks(self):
        valid = {"name": "example", "value": "synthetic-private-value", "domain": "is.muni.cz"}
        invalid = [
            '{"malformed":',
            "null",
            '"synthetic-private-value"',
            json.dumps({"cookies": [valid]}),
            json.dumps([valid, "synthetic-private-value"]),
            json.dumps([valid, {**valid, "domain": None}]),
            json.dumps([valid, {**valid, "expires": "synthetic-private-value"}]),
            json.dumps([valid, {**valid, "domain": "outside.example"}]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(tmp)
            for content in invalid:
                transport.cookie_path.write_text(content)
                transport.cookie_path.chmod(0o600)
                with self.subTest(content=content), self.assertRaises(AuthRequired) as caught:
                    transport._load()
                self.assertIn("--state-dir", str(caught.exception))
                self.assertNotIn(
                    "synthetic-private-value", "".join(traceback.format_exception(caught.exception))
                )
                self.assertEqual(len(transport.session.cookies), 0)
                self.assertEqual(transport.cookie_path.read_text(), content)
            transport.cookie_path.write_text(json.dumps([valid]))
            transport._load()
            self.assertEqual(len(transport.session.cookies), 1)

    def test_nonregular_session_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "session.json")
            path.symlink_to(Path(tmp, "missing.json"))
            with self.assertRaises(StateError):
                Transport(tmp)
            path.unlink()
            path.mkdir(mode=0o700)
            with self.assertRaises(StateError):
                Transport(tmp)

    def test_snapshot_stops_after_auth_failure_or_rate_limit_and_keeps_prior_reads(self):
        for failure in [AuthRequired("Expired"), RateLimited("Wait", status_code=429)]:
            with self.subTest(error=failure.code):
                client = Client(transport=Mock())
                client.courses = Mock(return_value=[{"code": "EX101"}, {"code": "EX102"}])
                syllabus = {"state": "not_exposed", "sections": []}
                client.syllabus = Mock(return_value=syllabus)
                client.ropots = Mock(side_effect=failure)
                client.forums = Mock()
                client.reservations = Mock()
                client.files = Mock()
                client.notes = Mock()
                result = client.snapshot()
                self.assertFalse(result["complete"])
                self.assertEqual(result["data"], {"EX101": {"syllabus": syllabus}})
                self.assertEqual(result["errors"][0]["code"], failure.code)
                client.syllabus.assert_called_once_with("EX101")
                for method in [client.forums, client.reservations, client.files, client.notes]:
                    method.assert_not_called()

    def test_exam_collection_stops_after_auth_failure_or_rate_limit(self):
        entries = [
            {"course_id": "1", "series_id": str(i), "source_url": f"https://is.muni.cz/{i}"}
            for i in range(3)
        ]
        for failure in [AuthRequired("Expired"), RateLimited("Wait", status_code=429)]:
            with self.subTest(error=failure.code):
                client = Client(transport=Mock())
                client.context = {"studium": "1"}
                page = SimpleNamespace(text="synthetic", url="https://is.muni.cz/example")
                client.transport.get.side_effect = [page, page, failure]
                with (
                    patch("ismu.services.r.exam_index", return_value={"series": entries}),
                    patch("ismu.services.p.reservations", return_value={"items": [], "text": ""}),
                ):
                    result = client.exams()
                self.assertFalse(result["complete"])
                self.assertEqual(len(result["series"]), 1)
                self.assertEqual(result["series_count"], 3)
                self.assertEqual(result["errors"][0]["code"], failure.code)
                self.assertEqual(client.transport.get.call_count, 3)

    def test_old_exception_imports_and_base_catches_remain_valid(self):
        self.assertIs(NewAuthRequired, OldAuthRequired)
        for cls in [
            InvalidInput,
            ParseError,
            NetworkError,
            RateLimited,
            StateError,
            UpstreamError,
            UnsupportedOperation,
            AttemptUncertain,
        ]:
            self.assertIsInstance(cls("Example"), ISMUError)
        with self.assertRaises(ParseError):
            parsers.ropots("Unrecognized page", "https://is.muni.cz/example")

    def test_transport_categorizes_errors_without_exposing_response_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(tmp, delay=0)
            for status, expected in [
                (403, UpstreamError),
                (500, UpstreamError),
                (429, RateLimited),
            ]:
                transport.session.request = Mock(return_value=Mock(status_code=status))
                with self.subTest(status=status), self.assertRaises(expected) as caught:
                    transport._request("https://is.muni.cz/auth/student/predmety", read_only=True)
                self.assertEqual(caught.exception.status_code, status)
            transport.session.request = Mock(side_effect=requests.Timeout("sensitive request"))
            with patch("ismu.transport.time.sleep"), self.assertRaises(NetworkError) as caught:
                transport._request("https://is.muni.cz/auth/student/predmety", read_only=True)
            self.assertNotIn("sensitive request", str(caught.exception))

    def test_rest_distinguishes_input_state_upstream_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(Client(tmp), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            headers = {"Authorization": "Bearer " + Path(tmp, "api-token").read_text()}
            url = f"http://127.0.0.1:{server.server_port}/v1/courses"
            try:
                for failure, status in [
                    (InvalidInput("Input"), 422),
                    (UnsupportedOperation("Unsupported"), 422),
                    (StateError("State"), 409),
                    (ParseError("Page"), 502),
                    (NetworkError("Network"), 502),
                    (UpstreamError("Upstream", status_code=403), 502),
                    (AuthRequired("Login"), 503),
                    (RateLimited("Limit", status_code=429), 503),
                ]:
                    with (
                        self.subTest(error=type(failure).__name__),
                        patch.object(Client, "courses", side_effect=failure),
                    ):
                        response = requests.get(url, headers=headers, timeout=3)
                        self.assertEqual(response.status_code, status)
                        self.assertEqual(response.json()["code"], failure.code)
                        self.assertEqual(response.json()["error"], type(failure).__name__)
                        if isinstance(failure, UpstreamError) and failure.status_code:
                            self.assertEqual(
                                response.json()["upstream_status"], failure.status_code
                            )
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
