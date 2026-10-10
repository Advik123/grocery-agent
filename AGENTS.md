# AGENTS.md

Instructions for coding agents working in this repository.

## Project overview

A Python/Flask backend that accepts POST requests and automates adding grocery
items to OurGroceries lists via Playwright.  Includes WhatsApp Cloud API webhook
integration with natural language parsing via Gemini 2.5 Flash.  WhatsApp is
the only planned client; the Swift iOS frontend and Siri App Intents phases are
unlikely to be built.

## Build / run / test commands

```bash
# Install dependencies (inside a venv)
pip install -r requirements.txt
playwright install chromium

# One-time login (opens headed browser, saves session to .browser-profile/)
python login.py

# Run the server
python app.py

# Run all tests (once tests/ directory exists)
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_parser.py

# Run a single test function
python -m pytest tests/test_parser.py::test_strips_whitespace

# Lint
ruff check .
ruff check --fix .

# Type-check
mypy .

# Syntax-check without running
python -m py_compile app.py
```

## File layout

```
app.py              - Flask server, /add endpoint and /whatsapp webhook routes
login.py            - one-time login helper (sync Playwright, headed browser)
playwright_agent.py - async Playwright automation (add_to_list)
store_config.py     - store name -> OurGroceries URL map (STORE_LISTS)
parser.py           - input validation & cleaning (parse_items)
message_parser.py   - WhatsApp text command parsing (parse_whatsapp_text)
whatsapp.py         - WhatsApp Cloud API payload parsing and replies
debug.py            - selector debugging utility
requirements.txt    - pinned dependency ranges
.env.example        - config template
.browser-profile/   - persistent Chromium profile (login session)
tests/              - pytest tests (create when adding tests)
legal-pages/        - static privacy/deletion pages for Meta app publishing
```

## Code style

### Formatting
- 4-space indentation, 88-char line limit (Black defaults).
- No trailing whitespace; single newline at end of file.

### Naming
- `snake_case` for functions, variables, modules, file names.
- `PascalCase` for classes only.
- Constants: `UPPER_SNAKE_CASE` (e.g. `STORE_LISTS`, `PROFILE_DIR`).
- Async functions use the same naming — no `async_` prefix.

### Type hints
- Annotate every function signature (parameters and return type).
- Use built-in generics: `dict[str, int]`, `list[str]`, not `Dict` / `List`.

### Imports
Group in this order, separated by one blank line:

1. Standard library (`asyncio`, `os`, `pathlib`)
2. Third-party (`flask`, `playwright`, `dotenv`)
3. Local modules (`parser`, `store_config`, `playwright_agent`)

Sort alphabetically within each group.  Use `import X` for stdlib and
`from X import Y` for third-party/local.  Example from `app.py`:

```python
import asyncio
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from parser import parse_items
from playwright_agent import add_to_list
from store_config import get_list_url
```

### Docstrings
- Every public function gets a one-line summary in `"""..."""` format.
- Use `*name*` for inline parameter references.

### Comments
- Header comment on each file: purpose + future phase notes.
- Inline comments explain *why*, not *what*.

## Error handling

- Validate external input at the boundary (Flask endpoint or parser).
- Raise `ValueError` for bad user input (parser), `RuntimeError` for
  infrastructure failures (missing login profile, expired session).
- Catch specific exceptions (`KeyError`, `ValueError`), never bare `except`.
- Flask endpoints return `jsonify({"error": "message"})` with 400 for client
  errors, 500 for server errors.  Never expose tracebacks to callers.
- Pattern in `app.py`: validate each piece sequentially, return early with 400
  on failure, wrap the Playwright call in its own try/except returning 500.

## Architecture

- `app.py` is the single entry point.  No additional Flask apps.
- `playwright_agent.py` is stateless — no module-level mutable state.
  Uses a persistent Chromium context at `.browser-profile/`.
- `store_config.py` is pure data + lookup.  To add a store, add one entry
  to `STORE_LISTS`.  No other files change.
- `parser.py` is pure — no I/O, no Flask/Playwright imports.
- `login.py` uses synchronous Playwright (not async) to simplify the
  blocking `input()` call while the browser is open.
- New endpoints go in `app.py`.  Import logic from other modules.

## Playwright selectors

OurGroceries uses a JS-rendered UI.  Key selectors (verified via debug.py):

| Element | Selector |
|---|---|
| Add item button | `button.addItemButton` |
| Item name input | `#addItemName` (type="search", placeholder="Find or add item") |

The flow: click `.addItemButton` → wait for `#addItemName` visible → fill → Enter.

## Testing conventions

- Use `pytest`.  Files: `tests/test_*.py`.  Functions: `test_<what_it_tests>`.
- `tests/test_message_parser.py` covers WhatsApp command parsing.
- `pytest.raises(ValueError)` for expected-error cases.
- Mock Playwright in `test_playwright_agent.py` — don't launch a real browser.
- Fixtures in `tests/conftest.py` or at the top of each test file.

```python
# test_parser.py
import pytest
from parser import parse_items

def test_strips_whitespace():
    assert parse_items({" eggs ": 2}) == {"eggs": 2}

def test_rejects_zero_quantity():
    with pytest.raises(ValueError, match="positive integer"):
        parse_items({"eggs": 0})
```

## Request flow

```
POST /add  →  parse_items()  →  get_list_url()  →  add_to_list()  →  200 OK
                (validate)      (store lookup)     (Playwright)
           ↓ bad input        ↓ unknown store    ↓ session error
             400               400                 500

GET /whatsapp  →  verify WA_VERIFY_TOKEN  →  return hub.challenge or 403

POST /whatsapp → parse_whatsapp_message() → parse_whatsapp_text()
                    (ignore receipts)        (store/items)
              → parse_items() → get_list_url() → add_to_list()
                 (validate)     (store lookup)    (Playwright)
              → send_whatsapp_reply() → 200 OK to Meta
```

## Environment

- Config via `python-dotenv` from `.env` (never commit — only `.env.example`).
- Env vars: `PORT` (default 5000), `FLASK_ENV` (default production).
- WhatsApp env vars: `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`,
  `WA_VERIFY_TOKEN`.
- `GEMINI_API_KEY` — Gemini API key for natural language message parsing (get free at aistudio.google.com)

## Phase roadmap

- **Phase 1** — Complete. Flask server with Playwright automation, WhatsApp Cloud API
  webhook integration, and natural language parsing via Gemini 2.5 Flash.
- **Phase 2** — Swift iOS frontend sends HTTP POST over LAN.  Do not add
  auth, CORS, or session state unless asked.
- **Phase 3** — Siri App Intents produce item dicts that flow through `parse_items`.

> **Note:** The WhatsApp number is the only client we plan to use.  Phases 2
> and 3 will most likely never be built, so don't design or add code around a
> future iOS app or Siri integration.

## Do not

- Introduce a database or ORM — stateless request-response server.
- Add logging libraries — `print()` or stdlib `logging` only.
- Create class hierarchies — modules are collections of functions.
- Auto-commit or auto-push — only commit when explicitly asked.

## Debugging

`debug.py` opens a headed browser and dumps the OurGroceries DOM (inputs,
buttons, dialogs) so you can find the right Playwright selectors.  Run it
when the page structure changes:

```bash
python debug.py
```

## Adding new files

1. Create with a header comment (purpose + future phases).
2. Add to this AGENTS.md under "File layout".
3. Update `requirements.txt` if new third-party dependencies are needed.
4. Run `ruff check .` and `mypy .` before considering the change done.
