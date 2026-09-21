"""Explicit ROPOT interaction, separate from read-only collection and REST.

Native single-page forms are supported. Hidden fields remain in private local
state; POSTs are never retried. A successful submission requires a receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from . import models
from . import parsers as p
from .errors import AttemptUncertain as AttemptUncertain
from .errors import InvalidInput, ParseError, StateError, UnsupportedOperation
from .questions import Answer, AnswerField, AnswerValue, Choice, Question
from .storage import check_private_file
from .transport import BASE, AuthRequired, ISMUError, Transport, private_write


class RopotTransport(Transport):
    def __init__(self, state_dir, entry_url, *, allow_writes=False):
        self.entry_url = entry_url
        self.allow_writes = allow_writes
        super().__init__(state_dir)

    def _validate_read(self, url):
        parsed = urlsplit(url)
        if parsed.hostname == "muni.islogin.cz" and parsed.path.startswith("/login/"):
            return
        expected = urlsplit(self.entry_url)
        actual_query = parse_qs(parsed.query.replace(";", "&"), keep_blank_values=True)
        expected_query = parse_qs(expected.query)
        if (
            parsed.hostname != "is.muni.cz"
            or parsed.path != expected.path
            or actual_query != expected_query
        ):
            raise UnsupportedOperation(
                "ROPOT requests must stay on the selected entry route and testurl."
            )

    def _validate_method(self, method, url):
        if method == "GET":
            return
        if method != "POST" or not self.allow_writes or urlsplit(url).hostname != "is.muni.cz":
            raise UnsupportedOperation(
                "ROPOT writes require an explicitly enabled attempt session."
            )
        self._validate_read(url)

    def exchange(self, *, data=None):
        result = self._request(
            self.entry_url, method="POST" if data is not None else "GET", data=data, read_only=True
        )
        # Public entry pages may first set a session cookie and ask for a same-URL refresh.
        # Follow that observed read-only bootstrap once; never replay a POST this way.
        bootstrap = p.soup(result.text)
        refresh = bootstrap.select_one('meta[http-equiv="refresh"]')
        if (
            data is None
            and refresh
            and refresh.get("content", "").strip() in ("0", "1")
            and not bootstrap.select_one("#app_content")
        ):
            time.sleep(1)
            result = self._request(self.entry_url, read_only=True)
        if self._is_login(result):
            raise AuthRequired("ROPOT session expired. Log in through the main CLI.")
        self._save()
        return result


def question_text(node):
    """Keep math image alternatives in readable text instead of losing equations."""
    copy = p.soup(str(node))
    for image in copy.select("img"):
        alt = image.get("alt") or image.get("title") or "[image; see source URL]"
        image.replace_with(" " + alt + " ")
    for hidden in copy.select("script,style,noscript,button,input,select,textarea"):
        hidden.decompose()
    return p.clean_text(copy)


def control(node):
    kind = node.get("type", "text").lower() if node.name == "input" else node.name
    value = (
        node.get("value", "on" if kind in ("radio", "checkbox") else "")
        if node.name != "textarea"
        else node.get_text()
    )
    parent_label = node.find_parent("label")
    result = {
        "name": node["name"],
        "type": kind,
        "value": value,
        "label": question_text(parent_label) if parent_label else "",
        "checked": node.has_attr("checked"),
        "required": node.has_attr("required"),
    }
    if node.get("maxlength"):
        try:
            result["maxlength"] = int(node["maxlength"])
        except (TypeError, ValueError):
            raise ParseError("Question field has an invalid length limit.") from None
        if result["maxlength"] < 0:
            raise ParseError("Question field has an invalid length limit.")
    if node.name == "select":
        result["multiple"] = node.has_attr("multiple")
        result["options"] = [
            {
                "value": o.get("value", p.text(o)),
                "label": p.text(o),
                "selected": o.has_attr("selected"),
            }
            for o in node.select("option")
            if not o.has_attr("disabled")
        ]
    return result


def attempt_view(html, url):
    doc = p.soup(html)
    main = doc.select_one("#app_content")
    if not main:
        raise ParseError("ROPOT page structure is missing.")
    form = main.select_one("#odpo_form_test")
    receipt = main.select_one(".zdurazneni.potvrzeni")
    submitted = bool(receipt and "odpovědi byly úspěšně odevzdány" in p.text(receipt))
    start = next(
        (
            f
            for f in main.select("form")
            if f.select_one("input[name=new]") and f.select_one("input[name=slozit]")
        ),
        None,
    )
    state = (
        "submitted" if submitted else ("active" if form else ("ready" if start else "unavailable"))
    )
    questions = []
    for question in main.select(".odpo_otazka_"):
        body = question.select_one(".odpo_otazka_telo")
        if body is None:
            raise ParseError(
                "Question body is missing; refusing a partial question representation."
            )
        fields = [
            control(e)
            for e in body.select("input[name],textarea[name],select[name]")
            if e.get("type", "").lower() != "hidden" and not e.has_attr("disabled")
        ]
        questions.append(
            {
                "id": question.get("id"),
                "number": question.get("data-cislo-otazky"),
                "title": p.text(question.select_one(".odpo_otazka_nazev_")),
                "text": question_text(body),
                "fields": fields,
                "images": [
                    {"url": urljoin(url, i.get("src", "")), "alt": i.get("alt", "")}
                    for i in body.select("img")
                ],
                "links": p.links(body, url),
            }
        )
    page_count = form.select_one("input[name=celkem_stran]") if form else None
    page_value = page_count.get("value", "") if page_count else ""
    declared = re.search(r"počet otázek:\s*(\d+)", p.text(main))
    all_questions_present = bool(
        declared and int(declared.group(1)) == len(questions) and questions
    )
    try:
        total_pages = int(page_value) if page_value else (1 if all_questions_present else None)
    except (TypeError, ValueError):
        raise ParseError("ROPOT page count is not recognized.") from None
    if total_pages is not None and total_pages < 1:
        raise ParseError("ROPOT page count is not recognized.")
    return {
        "state": state,
        "source_url": url,
        "title": p.text(doc.title),
        "text": question_text(main),
        "questions": questions,
        "question_count_on_page": len(questions),
        "total_pages": total_pages,
        "single_page_supported": total_pages == 1,
        "submission_confirmed": submitted,
        "receipt": p.text(receipt) if submitted else None,
    }


def form_payload(form, answers):
    """Serialize successful native controls, preserving repeated hidden fields."""
    if not isinstance(answers, dict):
        raise InvalidInput("Answers must be a JSON object keyed by discovered field name.")
    controls = [
        n
        for n in form.select(
            ".odpo_otazka_ input[name],.odpo_otazka_ textarea[name],.odpo_otazka_ select[name]"
        )
        if n.get("type", "").lower() != "hidden" and not n.has_attr("disabled")
    ]
    groups = {}
    for node in controls:
        groups.setdefault(node["name"], []).append(node)
    if set(answers) - set(groups):
        raise InvalidInput("Answers include a field not exposed by this question form.")
    data = [
        (n["name"], n.get("value", ""))
        for n in form.select("input[type=hidden][name]")
        if not n.has_attr("disabled")
    ]
    for name, nodes in groups.items():
        if not name.startswith("tst_"):
            raise UnsupportedOperation(
                "Unrecognized question field naming; an adapter update is required."
            )
        kinds = {control(n)["type"] for n in nodes}
        if len(kinds) != 1 or not kinds <= {
            "text",
            "number",
            "email",
            "url",
            "radio",
            "checkbox",
            "textarea",
            "select",
        }:
            raise UnsupportedOperation(
                "Unsupported question control; refusing a potentially incomplete answer."
            )
        kind = next(iter(kinds))
        allowed = None
        multiple = kind == "checkbox" or (kind == "select" and nodes[0].has_attr("multiple"))
        if kind in ("radio", "checkbox"):
            allowed = [n.get("value", "on") for n in nodes]
            current = [n.get("value", "on") for n in nodes if n.has_attr("checked")]
        elif kind == "select":
            options = [o for o in nodes[0].select("option") if not o.has_attr("disabled")]
            allowed = [o.get("value", p.text(o)) for o in options]
            current = [o.get("value", p.text(o)) for o in options if o.has_attr("selected")]
            if not current and options and not multiple:
                current = [allowed[0]]
        else:
            if len(nodes) != 1:
                raise UnsupportedOperation("Duplicate scalar answer field is unsupported.")
            current = [nodes[0].get_text() if kind == "textarea" else nodes[0].get("value", "")]
        value = answers.get(name, current)
        values = [value] if isinstance(value, str) else value
        if (
            not isinstance(values, list)
            or not all(isinstance(v, str) for v in values)
            or (not multiple and len(values) > 1)
        ):
            raise InvalidInput(
                "Each answer must be a string or a list of strings valid for that control."
            )
        if allowed is not None and any(v not in allowed for v in values):
            raise InvalidInput("A selected answer value is not one of the exposed options.")
        maximum = nodes[0].get("maxlength")
        if maximum and any(len(v) > int(maximum) for v in values):
            raise InvalidInput("An answer exceeds the field length limit.")
        data.extend((name, v) for v in values)
    return data


class RopotSession:
    """Persist one attempt per qref; write methods require allow_writes=True.

    Public demos use a nested, separate cookie jar. Questions are read from the
    last received form without starting, reopening, or refreshing an attempt.
    """

    def __init__(
        self,
        state_dir: str | Path,
        qref_path: str,
        *,
        public: bool = False,
        allow_writes: bool = False,
    ) -> None:
        if (
            not isinstance(qref_path, str)
            or unquote(qref_path) != qref_path
            or not qref_path.startswith(("/el/", "/do/"))
            or not qref_path.endswith(".qref")
            or ".." in PurePosixPath(qref_path).parts
            or any(c in qref_path for c in ("?", "#", ";", "\\", "\x00"))
        ):
            raise InvalidInput("Supply a plain /el/... or /do/... .qref path from a listed ROPOT.")
        self.qref_path = qref_path
        route = "/elearning/test_pruchod" if public else "/auth/elearning/test_pruchod_el_student"
        self.entry_url = BASE + route + "?" + urlencode({"testurl": qref_path})
        state_dir = Path(state_dir) / "public-ropot" if public else Path(state_dir)
        self.transport = RopotTransport(state_dir, self.entry_url, allow_writes=allow_writes)
        self.allow_writes = allow_writes
        self.cache_path = (
            state_dir
            / "ropot-attempts"
            / (hashlib.sha256(self.entry_url.encode()).hexdigest() + ".json")
        )

    def _load(self):
        if not check_private_file(self.cache_path, missing_ok=True):
            raise StateError(
                "No locally captured ROPOT page. Run inspect, then start if appropriate."
            )
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise StateError("ROPOT cache is unreadable; inspect the attempt in IS MU.") from None
        if (
            not isinstance(cached, dict)
            or not isinstance(cached.get("html"), str)
            or cached.get("pending") not in (None, "start", "save", "submit")
            or ("capture_id" in cached and not isinstance(cached["capture_id"], str))
        ):
            raise StateError("ROPOT cache structure is invalid; inspect the attempt in IS MU.")
        if cached.get("entry_url") != self.entry_url:
            raise StateError("ROPOT cache target mismatch.")
        return cached

    def _store(self, html, *, pending=None):
        private_write(
            self.cache_path,
            json.dumps(
                {
                    "entry_url": self.entry_url,
                    "html": html,
                    "pending": pending,
                    "capture_id": secrets.token_hex(16),
                },
                ensure_ascii=False,
            ).encode(),
        )

    def inspect(self) -> models.AttemptView:
        """Fetch entry information without selecting its start action."""
        if self.cache_path.exists():
            cached = self._load()
            if (
                cached.get("pending")
                or attempt_view(cached["html"], self.entry_url)["state"] == "active"
            ):
                raise StateError(
                    "A local attempt is active or uncertain; use questions or inspect it in IS MU first."
                )
        response = self.transport.exchange()
        view = attempt_view(response.text, response.url)
        self._store(response.text)
        return view

    def questions(self) -> models.AttemptView:
        """Read the last captured questions, including equations and field names."""
        cached = self._load()
        view = attempt_view(cached["html"], self.entry_url)
        view["pending_action"] = cached.get("pending")
        view["from_local_capture"] = True
        return view

    @staticmethod
    def _capture_id(cached):
        # Existing v0.3 captures remain usable until the next state transition.
        return cached.get("capture_id") or hashlib.sha256(cached["html"].encode()).hexdigest()

    def question_list(self) -> list[Question]:
        """Return convenient question objects from the local active capture; no HTTP."""
        cached = self._load()
        if cached.get("pending"):
            raise AttemptUncertain(
                "Inspect IS MU before building answers after an uncertain write."
            )
        view = attempt_view(cached["html"], self.entry_url)
        if view["state"] != "active":
            raise StateError("Question helpers require a locally captured active attempt.")
        origin = str(self.cache_path.resolve())
        capture = self._capture_id(cached)
        questions = []
        for question in view["questions"]:
            groups = {}
            for control_data in question["fields"]:
                groups.setdefault(control_data["name"], []).append(control_data)
            fields = []
            for index, (name, controls) in enumerate(groups.items()):
                first = controls[0]
                kind = first["type"]
                if kind not in {
                    "text",
                    "number",
                    "email",
                    "url",
                    "textarea",
                    "radio",
                    "checkbox",
                    "select",
                }:
                    raise UnsupportedOperation("Question helper does not support this control.")
                if any(c["type"] != kind for c in controls) or (
                    len(controls) > 1 and kind not in ("radio", "checkbox")
                ):
                    raise UnsupportedOperation("Ambiguous logical answer field.")
                options = (
                    first.get("options", [])
                    if kind == "select"
                    else (controls if kind in ("radio", "checkbox") else [])
                )
                fields.append(
                    AnswerField(
                        index=index,
                        kind=kind,
                        label=first["label"],
                        multiple=kind == "checkbox" or first.get("multiple", False),
                        choices=tuple(Choice(o["value"], o["label"]) for o in options),
                        required=any(c["required"] for c in controls),
                        maxlength=first.get("maxlength"),
                        _name=name,
                        _origin=origin,
                        _capture=capture,
                    )
                )
            questions.append(
                Question(
                    question["id"],
                    question["number"],
                    question["title"],
                    question["text"],
                    tuple(fields),
                )
            )
        return questions

    def question(self, number: int | str) -> Question:
        """Select by the exact displayed question number, without a network request."""
        if isinstance(number, bool) or not isinstance(number, (int, str)):
            raise InvalidInput("Supply the displayed question number as a string or integer.")
        matches = [q for q in self.question_list() if q.number == str(number)]
        if len(matches) != 1:
            raise InvalidInput("Question number must identify exactly one captured question.")
        return matches[0]

    def _answers(self, answers, cached):
        if answers is None:
            return {}
        if isinstance(answers, Mapping):
            return dict(answers)  # Backwards-compatible native field mapping.
        items = [answers] if isinstance(answers, Answer) else answers
        if (
            not isinstance(items, Sequence)
            or isinstance(items, (str, bytes))
            or not all(isinstance(a, Answer) for a in items)
        ):
            raise InvalidInput(
                "Supply an Answer, a list/tuple of Answers, or a native field mapping."
            )
        result = {}
        for answer in items:
            if answer._origin != str(
                self.cache_path.resolve()
            ) or answer._capture != self._capture_id(cached):
                raise StateError(
                    "Answer belongs to another or outdated capture; read the questions again."
                )
            if answer._name in result:
                raise InvalidInput("More than one answer supplied for the same field.")
            result[answer._name] = (
                list(answer._value) if isinstance(answer._value, tuple) else answer._value
            )
        return result

    def _write(self, action, answers=None):
        if not self.allow_writes:
            raise UnsupportedOperation(
                "Attempt actions require allow_writes=True or the explicit CLI action flag."
            )
        cached = self._load()
        if cached.get("pending"):
            raise AttemptUncertain(
                "A previous write has an uncertain outcome. Check IS MU before taking another action; automatic replay is blocked."
            )
        answers = self._answers(answers, cached)
        view = attempt_view(cached["html"], self.entry_url)
        doc = p.soup(cached["html"])
        if action == "start":
            if view["state"] != "ready":
                raise StateError(
                    "This captured ROPOT is not ready for a new attempt. Inspect its entry page first."
                )
            form = next(
                (
                    f
                    for f in doc.select("#app_content form")
                    if f.select_one("input[name=new]") and f.select_one("input[name=slozit]")
                ),
                None,
            )
            payload = [
                (n["name"], n.get("value", "")) for n in form.select("input[type=hidden][name]")
            ]
            button = form.select_one("button[type=submit],input[type=submit]")
        else:
            if view["state"] != "active" or not view["single_page_supported"]:
                raise UnsupportedOperation(
                    "Only active, single-page native question forms can currently be saved/submitted."
                )
            form = doc.select_one("#odpo_form_test")
            if not view["questions"]:
                raise ParseError("No recognizable question blocks; refusing an empty submission.")
            payload = form_payload(form, answers or {})
            button = (
                form.select_one("button[name=uloz],input[name=uloz]")
                if action == "submit"
                else form.select_one("button[name^=uloz_str_],input[name^=uloz_str_]")
            )
        if form.get("method", "get").lower() != "post" or button is None:
            raise ParseError("The expected native POST action is unavailable.")
        target = urljoin(self.entry_url, form.get("action", ""))
        self.transport._validate_read(target)
        if target != self.entry_url:
            raise ParseError("Form action changed; refusing to guess a write target.")
        targets = [v for k, v in payload if k == "testurl"]
        if targets != [self.qref_path]:
            raise ParseError("Form testurl does not match the selected ROPOT.")
        if button.get("name"):
            payload.append((button["name"], button.get("value", "")))
        # Persist intent before sending. A timeout/crash must never cause silent replay.
        self._store(cached["html"], pending=action)
        try:
            response = self.transport.exchange(data=payload)
            self._store(response.text, pending=action)
            result = attempt_view(response.text, response.url)
        except (ISMUError, OSError, ValueError) as exc:
            raise AttemptUncertain(
                "Write outcome is unknown; automatic replay is blocked. Inspect IS MU before continuing."
            ) from exc
        expected = (
            result["submission_confirmed"] if action == "submit" else result["state"] == "active"
        )
        if expected and action == "save" and answers:
            returned = form_payload(p.soup(response.text).select_one("#odpo_form_test"), {})
            for key, value in answers.items():
                expected_values = [value] if isinstance(value, str) else value
                if sorted(v for k, v in returned if k == key) != sorted(expected_values):
                    expected = False
        if not expected:
            self._store(response.text, pending=action)
            raise AttemptUncertain(
                "IS MU did not return the expected action result. Its response is captured privately; do not blindly retry."
            )
        self._store(response.text)
        result["action"] = action
        return result

    def start(self) -> models.AttemptView:
        """Start a new attempt. This can consume an attempt or start a timer."""
        return self._write("start")

    def save(
        self, answers: Answer | Sequence[Answer] | Mapping[str, AnswerValue]
    ) -> models.AttemptView:
        """Save supplied native answer fields, preserving other current values."""
        return self._write("save", answers)

    def submit(
        self,
        answers: Answer | Sequence[Answer] | Mapping[str, AnswerValue] | None = None,
        *,
        confirm: bool = False,
    ) -> models.AttemptView:
        """Submit once, requiring an explicit confirmation and server receipt."""
        if not confirm:
            raise UnsupportedOperation(
                "Submission requires confirm=True after reviewing the selected attempt and answers."
            )
        return self._write("submit", answers)
