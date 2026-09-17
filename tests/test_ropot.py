import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ismu import AttemptUncertain, ISMUError, RopotSession
from ismu import parsers as p
from ismu.ropot import attempt_view, form_payload

QREF = "/do/example/demo.qref"
ENTRY = """<main id="app_content"><form method="post">
<input type="hidden" name="new" value="1"><input type="hidden" name="slozit" value="1">
<input type="hidden" name="testurl" value="/do/example/demo.qref">
<button type="submit">Start</button></form></main>"""
ACTIVE = """<main id="app_content"><form id="odpo_form_test" method="post">
<input type="hidden" name="testurl" value="/do/example/demo.qref">
<input type="hidden" name="_" value="synthetic-token">
<input type="hidden" name="celkem_stran" value="1">
<div class="odpo_otazka_" id="question-1" data-cislo-otazky="1">
<section class="odpo_otazka_telo">Differentiate <img src="/formula" alt="f(x)=x^5">
<input type="hidden" name="test_sklad" value="tst_1">
<input type="text" name="tst_1_l_a_1" value="" maxlength="30"></section></div>
<div class="odpo_otazka_" id="question-2" data-cislo-otazky="2">
<section class="odpo_otazka_telo"><input type="hidden" name="test_sklad" value="tst_2">
<label><input type="checkbox" name="tst_2_c" value="A" checked>A</label>
<label><input type="checkbox" name="tst_2_c" value="B">B</label></section></div>
<button type="submit" name="uloz_str_0" value="Save">Save</button>
<button type="submit" name="uloz" value="Submit">Submit</button></form></main>"""
RECEIPT = """<main id="app_content"><div class="zdurazneni potvrzeni">
Vaše odpovědi byly úspěšně odevzdány.</div></main>"""


class RopotContracts(unittest.TestCase):
    def test_empty_page_count_requires_all_declared_questions(self):
        html = ACTIVE.replace('name="celkem_stran" value="1"', 'name="celkem_stran" value=""')
        self.assertFalse(attempt_view(html, "https://is.muni.cz")["single_page_supported"])
        html = html.replace(
            '<main id="app_content">', '<main id="app_content"><p>počet otázek: 2</p>'
        )
        self.assertTrue(attempt_view(html, "https://is.muni.cz")["single_page_supported"])

    def test_radio_select_and_textarea_serialization(self):
        html = """<form><div class="odpo_otazka_">
        <input name="tst_r" type="radio" value="a" checked><input name="tst_r" type="radio" value="b">
        <select name="tst_s"><option value="x">X</option><option value="y">Y</option></select>
        <textarea name="tst_a">Old text</textarea></div></form>"""
        form = p.soup(html).select_one("form")
        payload = form_payload(form, {"tst_r": "b", "tst_s": "y", "tst_a": "Line 1\nLine 2"})
        self.assertEqual(payload, [("tst_r", "b"), ("tst_s", "y"), ("tst_a", "Line 1\nLine 2")])
        self.assertIn(("tst_s", "x"), form_payload(form, {}))
        with self.assertRaises(ISMUError):
            form_payload(form, {"tst_r": ["a", "b"]})

    def test_save_must_return_the_supplied_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(ACTIVE)
            attempt.transport.exchange = Mock(
                return_value=SimpleNamespace(text=ACTIVE, url=attempt.entry_url)
            )
            with self.assertRaises(AttemptUncertain):
                attempt.save({"tst_1_l_a_1": "5*x^4"})
            self.assertEqual(attempt.questions()["pending_action"], "save")

    def test_unparseable_write_response_is_preserved_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(ENTRY)
            attempt.transport.exchange = Mock(
                return_value=SimpleNamespace(text="Changed upstream page", url=attempt.entry_url)
            )
            with self.assertRaises(AttemptUncertain):
                attempt.start()
            self.assertEqual(attempt._load()["html"], "Changed upstream page")
            self.assertEqual(attempt._load()["pending"], "start")

    def test_questions_preserve_equations_and_hide_tokens(self):
        view = attempt_view(ACTIVE, "https://is.muni.cz/elearning/test_pruchod")
        self.assertEqual(view["state"], "active")
        self.assertIn("f(x)=x^5", view["questions"][0]["text"])
        self.assertNotIn("synthetic-token", str(view))
        self.assertEqual(view["questions"][1]["fields"][0]["value"], "A")

    def test_payload_preserves_hidden_duplicates_and_replaces_answers(self):
        form = p.soup(ACTIVE).select_one("form")
        payload = form_payload(form, {"tst_1_l_a_1": "5*x^4", "tst_2_c": ["B"]})
        self.assertEqual([v for k, v in payload if k == "test_sklad"], ["tst_1", "tst_2"])
        self.assertIn(("tst_1_l_a_1", "5*x^4"), payload)
        self.assertNotIn(("tst_2_c", "A"), payload)
        self.assertIn(("tst_2_c", "B"), payload)
        self.assertNotIn(("uloz", "Submit"), payload)

    def test_answer_file_cannot_override_hidden_action_or_invent_option(self):
        form = p.soup(ACTIVE).select_one("form")
        for value in [
            {"_": "replacement"},
            {"testurl": "other"},
            {"tst_2_c": ["unknown"]},
            {"tst_1_l_a_1": 17},
        ]:
            with self.subTest(value=value), self.assertRaises(ISMUError):
                form_payload(form, value)

    def test_attempt_route_is_bound_to_one_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            for url in [
                "https://is.muni.cz/elearning/test_pruchod?testurl=%2Fdo%2Fother.qref",
                attempt.entry_url + ";new=1",
                "https://is.muni.cz/auth/student/zapis",
            ]:
                with self.assertRaises(ISMUError):
                    attempt.transport._validate_read(url)

    def test_writes_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True)
            attempt._store(ENTRY)
            attempt.transport.exchange = Mock()
            with self.assertRaises(ISMUError):
                attempt.start()
            attempt.transport.exchange.assert_not_called()

    def test_submit_requires_confirmation_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(ACTIVE)
            attempt.transport.exchange = Mock(
                return_value=SimpleNamespace(text=RECEIPT, url=attempt.entry_url)
            )
            with self.assertRaises(ISMUError):
                attempt.submit()
            attempt.transport.exchange.assert_not_called()
            result = attempt.submit({"tst_1_l_a_1": "5*x^4"}, confirm=True)
            self.assertTrue(result["submission_confirmed"])
            self.assertEqual(result["state"], "submitted")
            self.assertIn(("uloz", "Submit"), attempt.transport.exchange.call_args.kwargs["data"])

    def test_uncertain_post_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(ENTRY)
            attempt.transport.exchange = Mock(side_effect=ISMUError("Connection lost"))
            with self.assertRaises(AttemptUncertain):
                attempt.start()
            with self.assertRaises(AttemptUncertain):
                attempt.start()
            attempt.transport.exchange.assert_called_once()
            self.assertEqual(attempt.questions()["pending_action"], "start")

    def test_http_success_without_receipt_is_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(ACTIVE)
            attempt.transport.exchange = Mock(
                return_value=SimpleNamespace(text=ENTRY, url=attempt.entry_url)
            )
            with self.assertRaises(AttemptUncertain):
                attempt.submit(confirm=True)
            self.assertEqual(attempt.questions()["pending_action"], "submit")

    def test_multipage_submission_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True, allow_writes=True)
            attempt._store(
                ACTIVE.replace('name="celkem_stran" value="1"', 'name="celkem_stran" value="2"')
            )
            attempt.transport.exchange = Mock()
            with self.assertRaises(ISMUError):
                attempt.submit(confirm=True)
            attempt.transport.exchange.assert_not_called()

    def test_local_questions_make_no_network_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt = RopotSession(tmp, QREF, public=True)
            attempt._store(ACTIVE)
            attempt.transport.exchange = Mock()
            self.assertEqual(attempt.questions()["question_count_on_page"], 2)
            attempt.transport.exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
