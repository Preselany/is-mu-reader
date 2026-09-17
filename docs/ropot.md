# ROPOT questions and explicit attempt actions

`RopotSession` adds browser-free entry inspection, question extraction, draft saving and final submission for **native, single-page ROPOT forms**. It is separate from `Client.snapshot()` and the read-only REST server; neither starts attempts or submits answers.

The complete sequence was live-tested on MU's [public, ungraded mathematics/physics demonstration](https://is.muni.cz/do/rect/el/estud/prilohy/l_otazka/ukazka_moznosti_vyuziti_l.qref), using a separate anonymous cookie jar. That verifies the demonstrated native form protocol, not every faculty's graded test or interactive question widget. The initial course account's ROPOTs were still closed, so no course attempt was started or submitted.

## Python interface

Before using a custom client for an assessment, check the course conditions and obtain any required permission. See [permissions and responsible use](responsible-use.md). Software write flags do not grant academic authorization.

The convenient entry point accepts an item returned by the read-only listing:

```python
from ismu import Client

client = Client(".ismu-state")  # After ismu login
items = client.ropots("COURSE_CODE")["items"]
selected = items[0]  # Your application chooses the intended ROPOT
attempt = client.ropot(selected, allow_writes=True)  # No request yet
info = attempt.inspect()  # Entry only; no Start action
# Review this ROPOT's instructions, timing and attempt limits before proceeding.
attempt.start()  # Explicitly creates a real attempt and may start its timer

questions = attempt.question_list()  # Local question objects; no HTTP
question = questions[0]
print(question.number, question.text)
answer = question.answer("your answer")  # Local value; nothing sent or staged
attempt.save(answer)  # Sends the answer and verifies returned values

review = attempt.questions()  # Existing JSON view of current saved values
# After reviewing all answers:
receipt = attempt.submit(confirm=True)  # Submits the current saved form once
```

`question(number)` selects the exact displayed IS question number, for example
`attempt.question(1)`. It does not guess a number from list position.
`question_list()` returns all questions in the local active capture. Building
helpers before a captured active attempt raises `StateError`; uncertain writes
raise `AttemptUncertain`. Neither helper fetches or starts an attempt.

A question with one logical input accepts `question.answer(value)` directly.
Radio/checkbox options with the same field name count as one input. For several
blanks, select zero-based fields explicitly:

```python
question = attempt.question(1)
answers = [
    question.fields[0].answer("first blank"),
    question.fields[1].answer("second blank"),
]
attempt.save(answers)
```

Each field exposes `index`, `kind`, `label`, `multiple`, `choices`, `required`
and `maxlength`. Each choice has `label` and `value`. Pass the choice's value,
not its display label; use a list for multiple selections or `[]` to clear a
checkbox/multi-select group. A single `Answer` or a sequence of answers can be
passed to `save()` or `submit(answers, confirm=True)`.

Answers are immutable and bound to one captured form in one state directory.
After any new capture or successful write, create new answers from the current
questions. Stale answers, answers from another account/state directory and
duplicate answers for the same field are rejected before HTTP. `Answer` values
are not automatically staged or persisted; `save()` persists returned form
values. Low-level mappings below remain available but do not carry capture
identity. All forms still pass the same native-control validation.

### Native forms and backwards-compatible mappings

```python
from ismu import RopotSession, AttemptUncertain

# Use a qref_path returned by Client.ropots(course).
ropot = RopotSession(".ismu-state", "/el/FACULTY/SEMESTER/CODE/odp/example.qref")
info = ropot.inspect()  # Entry information only; no Start action
# Review the source instructions, availability, timing and attempt limits first.
```

To perform actions explicitly:

```python
ropot = RopotSession(
    ".ismu-state",
    "/el/FACULTY/SEMESTER/CODE/odp/example.qref",
    allow_writes=True,
)
questions = ropot.start()  # Uses the already inspected entry form
# Starting can create a real attempt and start its timer.

# Answer keys come from questions[*].fields[*].name, never guessed question numbers.
ropot.save({"tst_1_l_a_1": "your answer"})
review = ropot.questions()  # Local captured questions/current values; no HTTP action
receipt = ropot.submit(confirm=True)
assert receipt["submission_confirmed"]
```

`submit(answers, confirm=True)` can submit supplied answers directly; without `answers`, it submits the values in the last captured form. Unspecified fields keep their captured values. Callers remain responsible for reviewing answers and deciding when to start or submit. No solver or automatic answering is included.

| Method | Behavior |
|---|---|
| `inspect()` | GET entry page; capture instructions, current state and the native start form privately |
| `start()` | POST the observed start form once; return question text/controls |
| `questions()` | Read the last local capture without starting, reopening or refreshing anything |
| `question_list()` | Build immutable `Question`/`AnswerField` objects from the local active capture |
| `question(number)` | Select one question by its displayed number, without HTTP |
| `save(answers)` | POST the current form using its save button, verify returned answer values |
| `submit(answers=None, confirm=False)` | POST once with its submit button; require explicit confirmation and the server's successful-submission message |

For public demos, add `public=True`. This uses a separate `public-ropot/` cookie jar nested under the selected state directory, keeping the authenticated account session out of public demo requests. The qref path must be a plain `/el/... .qref` or `/do/... .qref` path. This interface does not accept arbitrary URLs, grant additional access, change time windows, or bypass required permissions/keys.

## CLI

`ropots CODE` remains the read-only list. Singular `ropot` is the attempt interface:

```bash
ismu ropot inspect /el/FACULTY/SEMESTER/CODE/odp/example.qref
ismu ropot start /el/FACULTY/SEMESTER/CODE/odp/example.qref --allow-attempt
ismu ropot questions /el/FACULTY/SEMESTER/CODE/odp/example.qref
ismu ropot save /el/FACULTY/SEMESTER/CODE/odp/example.qref --answers .ismu-state/answers.json --allow-attempt
ismu ropot submit /el/FACULTY/SEMESTER/CODE/odp/example.qref --confirm-submit
```

Add `--public` consistently for an anonymous public demonstration. Supply answers in a private JSON file, not command-line values. For example, after confirming those exact field names/options are present:

```json
{
  "tst_1_l_a_1": "5*x^4",
  "tst_2_c": ["option_a", "option_c"]
}
```

Strings serve scalar/radio controls. Lists serve checkbox and multi-select controls; an empty list clears selected options. Values for radio/checkbox/select fields must be among the actual form's options. Unknown fields, hidden-token overrides and oversized field values are refused. Remaining required questions are not automatically answered: review all fields before submitting.

## Question representation

An attempt view has `state`, `source_url`, `title`, readable `text`, `questions`, `question_count_on_page`, `total_pages`, `single_page_supported`, `submission_confirmed` and optional `receipt`. States are `unavailable`, `ready`, `active`, or `submitted`. `total_pages` is null when the page does not establish a page count. An empty native paging field is accepted as a single page only when all declared questions are present.

Each question carries its ID/number/title, text, exposed answer fields, source links and images. Mathematical expressions supplied as image alternatives are retained in text; images without alternatives remain explicit image references. This is not OCR or full rendering of arbitrary JavaScript widgets. Answer controls include type, name, current value, label, required/checked flags and, for selects, available options. Secret hidden fields are kept only in private state and never included in question JSON.

Native text, numeric, textarea, radio, checkbox and select controls are handled by the serializer. The live demo exercised algebraic text fields; the other native controls are covered by synthetic tests. The convenience objects added in 0.4 were tested offline; no additional live assessment was used. Uploads, custom JavaScript-only controls, access-key entry, multi-page save/submit, and reopening a previous attempt are not implemented. Multi-page questions can be detected, but saving/submitting them is refused to avoid silently omitting other pages.

## Write reliability

Every action is bound to the exact inspected route and qref. Hidden fields—including repeated `test_sklad` entries—are preserved, and only the selected submit button is added. Tokens and action names are read from the current form rather than invented. POSTs are not retried or replayed through 307/308 redirects.

Before a write, its intent is recorded in the private attempt cache. A network error, crash, changed form, or missing expected result leaves a pending action and raises `AttemptUncertain`. Subsequent writes are blocked. HTTP 200 alone is not treated as a successful submission. In this situation, inspect the real attempt in IS MU before deciding how to recover; do not automatically delete the cache and repeat the action. `questions()` exposes `pending_action` without sending a request.

Attempt caches live under `ropot-attempts/` in the corresponding private state directory. They contain full forms, answers and hidden tokens. Keep them private; never use live captures as public test fixtures. There is one local attempt per qref. Do not operate it concurrently from several processes or browser sessions.

MU's [ROPOT settings documentation](https://is.muni.cz/napoveda/elearning/popisy) explains that starting and submitting can have different attempt-counting and timing consequences. The caller must use the conditions shown for the selected ROPOT. The adapter does not infer that a course test is safe merely because an ungraded demonstration works.
