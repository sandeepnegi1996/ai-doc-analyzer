# AGENTS.md

Two-file Python app: `main.py` (FastAPI backend + all extraction logic) and `app.py` (Streamlit UI). Document definitions live in `usecases/*.json`.

## Setup / run

Local venv is Python 3.14 at `venv\Scripts\python.exe`.

```bash
GROQ_API_KEY=<key> uvicorn main:app --reload   # terminal 1, must be port 8000
streamlit run app.py                            # terminal 2
```

- `GROQ_API_KEY` is required at request time, not import time. The Groq client is built lazily via `get_client()` — without the key the import succeeds but `/analyze` returns 500. A `.env` in the project root is also loaded.
- `app.py` hardcodes `http://localhost:8000` for both requests. There is no env var for it; running the backend on any other port/host silently breaks the UI.
- `tesseract` must be on PATH for image uploads (installed at `C:\Program Files\Tesseract-OCR`). Verified working.

## The extractor is an LLM call, not regex (README is stale)

- Extraction is a Groq call to `openai/gpt-oss-120b` with `response_format` strict JSON schema, `temperature=0`, `max_tokens=500`. **Document text is sent to Groq's API.** Ignore the README's "runs entirely on your machine / zero API cost / no data leaves your machine" claims when editing docs or code.
- **`patterns` in `usecases/*.json` is dead config** — nothing in the code reads it. Only `name`, `key`, `type`, and `description` are consumed. Don't build on `patterns` expecting it to work.
- `groq` is **missing from `requirements.txt`**, so a clean `pip install -r requirements.txt` produces a backend that won't import. `requests` (used by `app.py`) is also unlisted; it only resolves because streamlit depends on it. `Pillow` is installed but never imported.

## Usecase config contract

A new document type = drop a JSON file in `usecases/`; the frontend dropdown picks it up automatically via `GET /usecases`.

- Filename stem must match `[A-Za-z0-9_-]+` or `load_usecase` 404s (`sale_deed` uses an underscore).
- Shape: `{"name": "Display Name", "fields": [{"key": ..., "type": ..., "description": ...}]}`.
- Supported `type` values are `string`, `float`, `int`, `date` (`date` is just a string; you are responsible for `YYYY-MM-DD` in the prompt text).
- **Adding a new numeric type means editing two places**: `type_map` in `build_json_schema` and the branch in `coerce_types`. Missing either silently degrades to string/no coercion.
- All field keys are emitted as `required` with `[type, "null"]` for Groq strict mode, so absent values come back `null` — never omit a key to make it optional.
- Don't create a field named `raw_text_preview`; the server overwrites that key with the extracted text.
- Type coercion is best-effort: on a `ValueError`/`TypeError` the raw model value is kept as-is rather than nulled.
- If the model returns unparseable JSON, all fields come back `null` (`json.JSONDecodeError` fallback in `extract_fields`).
- Prompt truncates document text to the first 6000 chars (`build_prompt`). Long documents are partially extracted by design.

## Verifying changes

There are **no tests, no linter, no formatter, no CI, and no task runner**. Verification is manual: start the backend, hit `/docs`, then drive the Streamlit UI. Don't invent a `pytest` command or claim tests pass.

Fixture gotchas in `invoices-img/` (verified by running the extraction helpers):

- `invoices-img/sale-deed-doc/Sale Deed.pdf` is a 13-page **scanned** PDF with 0 extractable characters. `extract_text_from_pdf` has no OCR fallback, so it returns all-null fields. Use it only to prove the PDF path is wired, never to check extraction quality.
- `sale-deed-doc/1.webp` OCRs fine (1232 chars) — Tesseract sniffs content even though the bytes are written to a temp file with a `.png` suffix. But `app.py`'s uploader only allows `pdf, png, jpg, jpeg, tiff, bmp`, so you **cannot** select the `.webp`/`.avif` fixtures through the UI. Convert to png/jpg first to exercise the `sale_deed` usecase end to end.
- `.avif` is not readable by Tesseract here — yields 0 chars.

## Repo quirks

- There is **no `.gitignore`**. `venv/` is untracked by luck of history, but `__pycache__/main.cpython-314.pyc` is **tracked** and shows as modified on nearly every run — ignore that diff noise, and don't commit refreshed `.pyc` files.
- `main.py` has uncommitted local edits relative to the last commit; don't assume `HEAD` reflects the working tree.
- No packaging, no config module, no `src/` layout: root-level scripts only. Keep new code in the existing two files or add plainly-named root modules.
