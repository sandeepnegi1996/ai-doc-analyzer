# AGENTS.md

Three-file Python app: `extractor.py` (all business logic, no HTTP/FastAPI), `main.py` (thin FastAPI shell, transport only), `app.py` (Streamlit UI). Document definitions live in `usecases/*.json`.

## Setup / run

Local venv is Python 3.14 at `venv\Scripts\python.exe`.

```bash
GROQ_API_KEY=<key> streamlit run app.py   # the UI; no backend needed
```

`app.py` calls `extractor.analyze_document()` in-process. `main.py` still exists as an optional HTTP API but nothing depends on it — you no longer need port 8000 for the UI.

- `GROQ_API_KEY` is required at call time, not import time. The Groq client is built lazily via `get_client()` — without the key the import succeeds but extraction raises `MissingApiKeyError`. A `.env` in the project root is also loaded.
- **Key precedence** is resolved in `app.py:load_api_key()`: real env var (incl. `.env`) first, then `st.secrets`, copied into `os.environ` because `get_client()` only reads the env. This is what makes Streamlit Cloud work — Cloud secrets never reach the process env. `st.secrets` access is wrapped in try/except because it raises when no `secrets.toml` exists; without that guard a keyless local run tracebacks instead of showing the warning banner. Do not move this into `extractor.py` — that module must stay free of Streamlit imports.
- `.env` and `.streamlit/secrets.toml` are gitignored; only `.env.example` and `.streamlit/secrets.toml.example` (placeholders) are tracked. Don't commit a real key.
- `tesseract` must be on PATH for image uploads (installed at `C:\Program Files\Tesseract-OCR`). Verified working. On Streamlit Cloud it comes from `packages.txt` (`tesseract-ocr`) — don't remove that file.

## Error contract

`extractor.py` never raises `HTTPException`; it raises `ExtractorError` subclasses (`UnknownUsecaseError`, `MissingApiKeyError`, `UnsupportedFileError`). `main.py` maps them to 404/500/422. Any new error in the extractor must subclass `ExtractorError` or the API will 500 with an unhandled traceback.

The pipeline is bytes -> text -> Groq -> dict. `extract_fields(text, usecase)` and `analyze_document(...)` accept either a usecase key string or an already-loaded config dict, so callers can validate the key before doing expensive OCR.

## The extractor is an LLM call, not regex

- Extraction is a Groq call to `openai/gpt-oss-120b` with `response_format` strict JSON schema, `temperature=0`, `max_tokens=2000`. **Document text is sent to Groq's API** — not local, not zero-cost. The README states this correctly; keep it that way if you edit either doc.
- **`patterns` in `usecases/*.json` is dead config** — the key is present in the shipped files but nothing in the code reads it. Only `name`, `key`, `type`, `description`, and `items` are consumed. Don't build on `patterns` expecting it to work.
- `requirements.txt` is split into a required block (`streamlit`, `groq`, `python-dotenv`, `PyMuPDF`) and an optional block for `main.py` (`fastapi`, `uvicorn`, `python-multipart`). `groq` **must** stay in the required block — `extractor.py` imports it at module scope, so a Cloud deploy missing it crashes on import. `Pillow` and `requests` were removed: nothing imports either.

## Usecase config contract

A new document type = drop a JSON file in `usecases/`; the Streamlit dropdown picks it up automatically via `extractor.list_usecases()`.

- Filename stem must match `[A-Za-z0-9_-]+` or `load_usecase` raises `UnknownUsecaseError` (`sale_deed` uses an underscore).
- Shape: `{"name": "Display Name", "fields": [{"key": ..., "type": ..., "description": ...}]}`. An optional `items` array of the same field shape is returned as a JSON array under `"items"`.
- Supported `type` values are `string`, `float`, `int`, `date` (`date` is just a string; you are responsible for `YYYY-MM-DD` in the prompt text).
- **Adding a new numeric type means editing two places**: `type_map` in `build_json_schema` and the branch in `coerce_types`. Missing either silently degrades to string/no coercion.
- All field keys are emitted as `required` with `[type, "null"]` for Groq strict mode, so absent values come back `null` — never omit a key to make it optional.
- Don't create a field named `raw_text_preview`; `extract_fields` overwrites that key with the extracted text.
- Type coercion is best-effort: on a `ValueError`/`TypeError` the raw model value is kept as-is rather than nulled.
- If the model returns unparseable JSON, all fields come back `null` (`json.JSONDecodeError` fallback in `extract_fields`).
- Prompt truncates document text to the first 6000 chars (`build_prompt`). Long documents are partially extracted by design.

## Verifying changes

There are **no tests, no linter, no formatter, no CI, and no task runner**. Don't invent a `pytest` command or claim tests pass. Three manual levels, cheapest first:

1. **Logic, no API** — call the helpers directly from `venv\Scripts\python.exe`: `extractor.extract_document_text(bytes, name)` for OCR/PDF only, `extractor.build_prompt` / `build_json_schema` / `coerce_types` for pure functions.
2. **Full pipeline** — `extractor.analyze_document(...)` (live Groq call).
3. **UI script** — `streamlit run app.py`, or headless with Streamlit's own test harness:
   ```python
   from streamlit.testing.v1 import AppTest
   at = AppTest.from_file(r"D:\code\doc-analyzer-mvp\app.py").run()
   assert not at.exception, at.exception.value
   ```
   Use an **absolute** path — `AppTest` resolves relative paths against the *calling* file, so a script in `%TEMP%` fails with `FileNotFoundError`. `at.warning` / `at.error` / `at.warning[i].value` are the way to assert on UI state without a browser. The "missing ScriptRunContext" log line in bare mode is harmless.

A known-good end-to-end check: `analyze_document(open('invoices-img/5.png','rb').read(), '5.png', 'invoice')` should return `invoice_number='INV-10012'`, `total=1699.48` (float), `date='2021-03-26'`. Verified against the live Groq API.

Key resolution has three paths, all verified via `AppTest` — check the one you changed:

| Setup | Expected |
|---|---|
| `GROQ_API_KEY` env var, or `.env` | resolves, no warning |
| only `.streamlit/secrets.toml` (mask the env with `""`; `load_dotenv` uses `setdefault` so `.env` won't override it) | resolves from `st.secrets` into `os.environ`, no warning — this is the Cloud path |
| nothing configured | no exception, key unset, warning banner shown |

Fixture gotchas in `invoices-img/` (re-verified against the current helpers):

- `invoices-img/sale-deed-doc/Sale Deed.pdf` is a 13-page **scanned** PDF with 0 extractable characters. `extract_text_from_pdf` has no OCR fallback, so it returns all-null fields. Use it only to prove the PDF path is wired, never to check extraction quality.
- `sale-deed-doc/1.webp` OCRs fine (~1230 chars; the exact count drifts by a character or two between runs, so don't assert an exact number) — Tesseract sniffs content even though the bytes are written to a temp file with a `.png` suffix. But `app.py`'s uploader only allows `pdf, png, jpg, jpeg, tiff, bmp`, so you **cannot** select the `.webp`/`.avif` fixtures through the UI. Convert to png/jpg first to exercise the `sale_deed` usecase end to end.
- `6.avif` is not readable by Tesseract here — yields 0 chars.

## Repo quirks

- **`.gitignore` exists** and covers `.env`, `.streamlit/secrets.toml`, `__pycache__/`, `*.pyc`, `venv/`. No `.pyc` is tracked any more, so the old "modified .pyc on every run" diff noise is gone.
- **`extractor.py`, `packages.txt`, and `.streamlit/secrets.toml.example` are still untracked** — the split out of `main.py` has not been committed. `main.py` also has large uncommitted edits relative to the last commit; don't assume `HEAD` reflects the working tree.
- `invoices-img/` **is** tracked (8 fixture files, ~binary). They're the only way to test extraction; don't delete them, and don't add more casually.
- `usecases/` ships 3 configs: `invoice` (3 fields), `sale_deed` (4 fields), `sales_order_ub` (12 fields + a 6-field `items` array — the only usecase that exercises the line-item table path).
- No CI, no `.streamlit/config.toml`, no packaging, no config module, no `src/` layout: root-level scripts only. Business logic belongs in `extractor.py`; `main.py` and `app.py` are transport/UI only. New root modules should be plainly named.
