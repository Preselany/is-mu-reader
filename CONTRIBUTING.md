# Contributing

This project is a small, unofficial access adapter with read-only collection and separate explicit ROPOT actions. Keep changes scoped to an observed adapter, a pure parser, or its public interface. The project uses the [MIT license](LICENSE); contributions are expected under the same license.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python tools/generate_models.py --check
mypy --follow-imports=silent --ignore-missing-imports --warn-unused-ignores tests/typing/public_api.py
ruff check .
ruff format --check .
python -m openapi_spec_validator docs/openapi.json
python -m build
```

On Windows, create the venv with `py -3 -m venv .venv` and install with `.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Run the Python checks with `.venv\Scripts\python.exe`, and use `.venv\Scripts\ruff.exe` / `.venv\Scripts\mypy.exe` for those tools. Activation is optional.

CI runs on `ubuntu-latest` and `windows-latest` for Python 3.11–3.13. Windows storage tests inspect real ACLs and reject broad grants; POSIX tests inspect modes. A clean wheel installation also checks native CLI entry points, timezone imports and session persistence. Run `python tools/check_wheel.py` after building to reproduce it locally (installation may download dependencies). Never replace the Windows ACL check with `chmod`, which does not provide equivalent protection on Windows.

Run `ruff format .` after editing. Tests are standard-library `unittest` and use synthetic HTML/XML plus mocked transports. Tests do not need real accounts or network access to IS MU. The REST security test uses a temporary loopback server. CI uses ordinary `push`/`pull_request` events and no account secrets, following the [GitHub Python workflow documentation](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).

## Adding a reader

1. Establish the upstream request and response shape using your own authorized account and the required approvals in [permissions and responsible use](docs/responsible-use.md).
2. Add only the narrowly required read route/query fields to the transport, including observed redirects.
3. Implement parsing in `parsers.py` with a minimal synthetic fixture demonstrating the important behavior.
4. Add the client operation, preserving provenance, null/locked states and incomplete collection evidence.
5. Update CLI/REST wiring as needed, the OpenAPI response model, data semantics and changelog. Regenerate public `TypedDict` definitions with `python tools/generate_models.py` and format them with Ruff; do not hand-edit `models.py`. Annotate the public method and add it to `tests/typing/public_api.py`.
6. Run the offline checks. If live verification is necessary, keep its data and credentials outside the repository.

A successful status code alone is not enough: a login form, access-denied page or suppressed list must not become a silent empty result. A GET link may mutate state, so never broaden the allowlist to all URLs under `/auth/`. Enrolment/registration changes and posting are out of scope. ROPOT writes belong only in the separate explicit attempt module; never enable them in the scanner or read-only REST server. Extend form controls with tests for serialization and ambiguous write outcomes.

## Fixtures and bug reports

Use invented IDs, names, email addresses and content. Prefer a short fixture you construct from the structural pattern over a captured page. Never commit cookies, passwords, login form tokens, gradebooks, student names, private posts, downloaded teaching material or HAR captures. Public reports should contain the command shape, package/Python versions, error class and a minimal sanitized reproduction, not a full snapshot.

Parser tests should cover an actual ambiguity or failure boundary: date conversion, variants, partial results, changed markup, authentication, path rejection or forbidden actions. Do not add tests that merely mirror the code line by line.

## Packaging and releases

The distribution name is `is-mu-reader`; the import is `ismu`; the console command is `ismu`. Distribution builds are local artifacts. No package registry publication or deployment is configured.

Before a release, check the changelog and public response schema, review the source archive for private data, and test a wheel in a clean virtual environment. MIT covers this source tree, not any third-party course content collected through it.
