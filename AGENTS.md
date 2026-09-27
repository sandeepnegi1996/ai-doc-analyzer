# AGENTS.md

Four-file Python app: `extractor.py` (all business logic, no HTTP/FastAPI), `main.py` (thin FastAPI shell, transport only), `app.py` (Streamlit UI), `config.py` (provider/model selection). Document definitions live in `usecases/*.json`. LLM backends live in `providers/`. UI result formatting lives in `result_tables.py`. Tests live in `tests/`.

## Setup / run

Local venv is Python 3.14 at `venv\Scripts\python.exe`.

```bash
GROQ_API_KEY=<key> streamlit run app.py   # the UI; no backend needed
venv\Scripts\python -m pytest              # the suite; offline, no key needed
```

`pip install -r requirements-dev.txt` for `pytest` (test-only; see "Verifying changes").

`app.py` calls `extractor.analyze_document()` in-process. `main.py` still exists as an optional HTTP API but nothing depends on it — you no longer need port 8000 for the UI.

- API keys are required at call time, not import time. The client is built in the provider's `__init__` when `get_llm_provider()` is called — without the key the import succeeds but extraction raises `MissingApiKeyError`. A `.env` in the project root is also loaded.
- **Key precedence** is resolved in `app.py:load_api_key()`: real env var (incl. `.env`) first, then `st.secrets`, copied into `os.environ` because the providers read the env at construction time. This is what makes Streamlit Cloud work — Cloud secrets never reach the process env. `st.secrets` access is wrapped in try/except because it raises when no `secrets.toml` exists; without that guard a keyless local run tracebacks instead of showing the warning banner. Do not move this into `extractor.py` — that module must stay free of Streamlit imports.
- The key names in `app.py` are Groq-specific (`GROQ_API_KEY`) even though the provider is pluggable; `app.py` was not part of the provider refactor. It still resolves the Groq key unconditionally, so a local Ollama run works only because Ollama needs no key.
- **`config.py` reads `LLM_PROVIDER` at import time, but resolves the model per call.** `LLM_PROVIDER` is captured when `config` is first imported, so mutating `os.environ` later will not switch provider. `resolve_model()` is the opposite: it reads the env on every call, so `GROQ_MODEL`/`OLLAMA_MODEL`/`OPENAI_MODEL` change without a restart. Only the API key is resolved at call time by the providers. Switching provider in a running Streamlit session needs a restart.
- **The model is configured per provider, not globally.** `config.PROVIDER_MODEL_ENV` maps `groq`→`GROQ_MODEL`, `ollama`→`OLLAMA_MODEL`, `openai`→`OPENAI_MODEL`, and `resolve_model()` prefers that variable, then the `LLM_MODEL` single-override escape hatch, then `PROVIDER_DEFAULT_MODELS`. Don't collapse the three back into one variable — that is what let a Groq model id be sent to a local Ollama server. `LLM_MODEL` itself is **not** a module constant any more (it was removed); `main.py` calls `resolve_model()` for `/v1/models`. Anything reading a model must go through `resolve_model(provider)`.
- `.env` and `.streamlit/secrets.toml` are gitignored; only `.env.example` and `.streamlit/secrets.toml.example` (placeholders) are tracked. Don't commit a real key.
- `tesseract` must be on PATH for image uploads (installed at `C:\Program Files\Tesseract-OCR`). Verified working. On Streamlit Cloud it comes from `packages.txt` (`tesseract-ocr`) — don't remove that file.

## Error contract

`extractor.py` never raises `HTTPException`; it raises `ExtractorError` subclasses (`UnknownUsecaseError`, `MissingApiKeyError`, `UnsupportedProviderError`, `EmptyDocumentError`, `OversizedDocumentError`, `UnsupportedFileError`, `LLMFailureError`). `main.py` maps them to 404/500/422/401/413/503. Any new error in the extractor must subclass `ExtractorError` or the API will 500 with an unhandled traceback.

There are **two** error hierarchies, and crossing between them is deliberate. `providers/base.py` defines `ProviderError` + `MissingApiKeyError`/`UnsupportedProviderError`/`ModelMismatchError`/`EmptyResponseError`; `extractor.py` defines its own `ExtractorError` family for transport. `_acquire_provider()` is the **only** translation point: it maps `MissingApiKeyError` → `MissingApiKeyError`, and both `UnsupportedProviderError` and `ModelMismatchError` → the extractor's `UnsupportedProviderError`. `extract_fields` maps `EmptyResponseError` → `LLMFailureError`. `config.ModelConfigError` is a `ValueError`; `get_llm_provider()` is the only place that converts it to a `ProviderError`.

> **Shadowing trap:** `extractor.py` imports the provider errors under `Provider*` aliases on purpose. It defines its own `UnsupportedProviderError` *after* the import block, so a bare `from providers import UnsupportedProviderError` would be silently shadowed and the `except` clause in `_acquire_provider` would never match — the error would escape as a non-`ExtractorError` and 500 with a traceback. Keep the aliases if you rename anything here.

The pipeline is bytes -> text -> LLMProvider -> dict. `extract_fields(text, usecase)` and `analyze_document(...)` accept either a usecase key string or an already-loaded config dict, so callers can validate the key before doing expensive OCR.

## The provider layer

`extractor.py` has **no** provider-specific code. It builds the prompt and the schema, then calls `provider.extract(prompt=..., json_schema=..., max_tokens=..., temperature=...)`. A provider connects, sends, receives and returns — it never sees a document and never builds a prompt. Don't move prompt construction into a provider; that is the duplication this layer exists to prevent.

- `config.py` holds `LLM_PROVIDER` (`groq`|`ollama`|`openai`), `OLLAMA_BASE_URL`, and the per-provider model tables, all env-driven. Adding a backend = new module in `providers/` + one entry each in `_PROVIDER_CLASSES` / `_PROVIDER_KWARGS` in `providers/__init__.py` + a default in `config.PROVIDER_DEFAULT_MODELS` + a `<NAME>_MODEL` entry in `config.PROVIDER_MODEL_ENV` + the SDK in `requirements.txt`. Nothing in `extractor.py` changes. The README's "Adding a provider" section is the checklist.
- **Model defaults are per-provider, not global.** A model id is only meaningful for the backend that serves it, so `PROVIDER_DEFAULT_MODELS` maps `groq`→`openai/gpt-oss-120b`, `ollama`→`llama3.1`, `openai`→`gpt-4o-mini`. `resolve_model()` picks the per-provider env var, then `LLM_MODEL`, then the default — and rejects a namespaced `vendor/model` id aimed at `ollama`/`openai` (`config.UNSLUGGED_PROVIDERS`) with a `ModelConfigError` naming the variable to fix. Do not collapse this back to one global default; that regressed `LLM_PROVIDER=ollama` into sending Groq's model id to a local Ollama server.
- **`build_json_schema` returns a *bare* JSON Schema** (`{"type":"object","properties":...,"required":[...],...}`) — no `response_format` envelope, no `name`, no `strict`. Each provider wraps it in its own wire format. This is deliberate: a `json_schema` envelope in `extractor.py` was the double-wrapping bug that made every live Groq call fail with `400 ... 'response_format.json_schema.name' : property 'name' is missing`. `tests/test_extractor_pipeline.py::test_schema_is_bare_so_a_provider_never_double_wraps_it` guards it.
- `LLMProvider` exposes `schema_name`/`strict`/`display_name` class attributes and an abstract `capabilities` property returning a frozen `ProviderCapabilities(structured_output, json_schema, vision)`. `call_llm` sends the schema only when `capabilities.json_schema` is true, and passes `json_schema=None` otherwise — then `OpenAICompatibleProvider.build_response_format(None)` degrades to `{"type": "json_object"}` plus the prompt's own JSON instruction. Capabilities describe what this app may rely on, so `vision` is `False` everywhere: documents always reach the LLM as text after OCR/PDF parsing. Flip it when a scanned-PDF/vision path lands.
- **Groq, Ollama and OpenAI all speak the OpenAI chat-completions API**, so `extract()` lives once in `OpenAICompatibleProvider` (request body, `read_content`, empty-content check). A provider module is now just credentials, client, `capabilities`, and `supports_strict_schema` (Ollama's endpoint rejects the extra `strict` key; Groq/OpenAI send it). Subclass `OpenAICompatibleProvider` unless the API is not OpenAI-shaped.
- Providers return the model's **raw response string**; `json.loads` + `coerce_types` happen in `extractor.py`, so a malformed response degrades to all-null fields rather than an exception.

## The extractor is an LLM call, not regex

- Extraction is an LLM call with strict JSON schema, `temperature=0`, `max_tokens=2000`. By default that is a Groq call to `openai/gpt-oss-120b`, so **document text is sent to Groq's API** — not local, not zero-cost. The README states this correctly; keep it that way if you edit either doc. `LLM_PROVIDER=ollama` with a local model is the only fully-local path.
- The pipeline stages are separate functions and each one is a test target: `build_prompt` → `build_json_schema` → `call_llm(provider, text, config)` → `parse_llm_response(raw, config)` → `coerce_types` → `validate_result`. `extract_fields(text, usecase)` runs the last five; `analyze_document` = load usecase → extract text → `extract_fields`.
- `parse_llm_response` **never raises**: a malformed response, or valid JSON that isn't an object (`[1,2]`, `"text"`, `null`), degrades to an all-null dict. Don't "improve" it into something that throws — a partial answer beats a 503.
- `validate_result` normalises *keys* only: every declared field present (missing → `None`), `items` a list of dicts with the declared item keys, undeclared keys dropped. It deliberately does **not** re-check types, because `coerce_types` intentionally keeps a value it could not coerce; validating types here would null those.
- `coerce_types` skips non-dict entries in `items` instead of raising `AttributeError` — a schema-less model can return junk, and `validate_result` drops it afterwards.
- **`patterns` in `usecases/*.json` is dead config** — the key is present in the shipped files but nothing in the code reads it. Only `name`, `key`, `type`, `description`, and `items` are consumed. Don't build on `patterns` expecting it to work.
- `requirements.txt` is a single required block (`streamlit`, `groq`, `openai`, `pandas`, `python-dotenv`, `PyMuPDF`); the optional block for `main.py` (`fastapi`, `uvicorn`, `python-multipart`) is gone. `groq` and `openai` **must** both stay: `providers/__init__.py` imports all three provider modules eagerly, so a Cloud deploy missing either package crashes on import even if that provider is not selected. `pandas` is now imported directly by `result_tables.py`, so it is listed explicitly rather than relied on as a transitive Streamlit dependency. `Pillow` and `requests` were removed: nothing imports either. `requirements-dev.txt` is a separate, test-only block — never add a test-only package to `requirements.txt`, or Streamlit Cloud installs it for nothing.

## UI results (app.py + result_tables.py)

- **Use `st.dataframe`, never `st.table`.** `st.table` renders a static HTML table with *no toolbar at all* — no sort, search, full-screen or download. That was the reason the old results page had no download button. `st.dataframe` gives all of that from its own toolbar, and the explicit `st.download_button`s cover the full result.
- **Never stringify values.** The old code did `str(value)` for every cell, which left-aligned numbers and made quantities sort as 1, 10, 2. `frame_from_rows` keeps native dtypes and `column_config_for` types the columns from the usecase config (`int`→`NumberColumn` `%d`, `float`→`NumberColumn` `%.2f`). Test with 1, 2 and 10 in that order — as text they sort 1, 10, 2, so the assertion only passes if the column is genuinely numeric.
- `st.column_config.*Column(...)` returns a **plain dict**, not an object — assert on `["type_config"]["type"]` in tests, not on the class name.
- A `date` field is a plain string in this app, so it stays a `TextColumn`; a `DateColumn` needs real date objects and would error.
- **The results are ONE table, not two.** Document fields and line-item fields sit side by side, one row per line item, with document fields repeated down the rows — the flattened shape an ERP export uses, so the whole result copies into a spreadsheet in one action. Do not split it back into a details table and an items table: that meant two copy operations and two things to keep in sync visually.
- **All-empty columns are hidden by default** (`drop_empty_columns`), with the count disclosed in the caption and a "Show fields that were not found" checkbox to bring them back. This is the single biggest readability lever in a wide table: an all-blank column still claims horizontal space and squeezes the columns that have data (typically a third of a sales order's fields). Never drop them *silently*, and the CSV/JSON exports always keep every field regardless of the toggle.
- **Column widths are content-driven** (`_width_for`): small/medium/large from the longest value in the column. Streamlit's presets are ~75/200/400px at ~7px per char, so the cutoffs (10/28 chars) are the longest string that still fits on one line. A long free-text field in a narrow column is exactly what stretched a row to ~15 lines tall in the original UI.
- `combined_table` returns `(frame, fields)`; the second element is the usecase field dicts in column order, for `column_config_for` to zip labels against types.
- `len(df)` is the **row** count. Counting columns needs `len(df.columns)` — this bug shipped once in the results caption ("0 fields"), so the caption test asserts against `len(shown.columns)`.
- Document rows are numbered from 1 in a leading `#` column, so a row stays referable after sorting or filtering.
- Column labels go through `field_label`, which restores acronyms, because `key.replace("_"," ").title()` produced headers like "Item Code Sku". Add to `ACRONYMS` rather than special-casing at the call site.
- Download filenames are sanitised by `file_stem` (unsafe chars collapse to a single `_`), because the uploaded name reaches the `Content-Disposition` header.
- `render_results` reads from `st.session_state.analysis`, not from the click handler, so the tables survive unrelated widget interactions. Anything wanting to re-render results must set that key.
- Presentation helpers live in `result_tables.py`, not `app.py`: importing `app.py` executes the whole page, so tests could not reach helpers defined there. Keep new formatting helpers in `result_tables.py`.

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

There is a pytest suite and **no linter, no formatter, no CI, and no task runner**. `requirements.txt` is for the app; `requirements-dev.txt` is for tests (`pytest` only). `pytest.ini` sets `testpaths = tests` and `pythonpath = .`, so `import extractor` works under a bare `pytest` too.

Run this first — it is offline, needs no key, and takes under a second:

```bash
venv\Scripts\python -m pytest
```

- `tests/conftest.py` holds the doubles: `FakeClient` (records the request a provider would have sent) and `StubProvider` (a real `LLMProvider` for extractor tests). The `provider_factory` fixture is parameterised over Groq/Ollama/OpenAI, so each contract test in `tests/test_provider_contract.py` runs three times — **add a new provider class to `PROVIDERS` there or it is untested**.
- `tests/test_config_models.py` covers `resolve_model` precedence and the mismatch guard; it clears `GROQ_MODEL`/`OLLAMA_MODEL`/`OPENAI_MODEL`/`LLM_MODEL` first, because a developer's `.env` may set them.
- `tests/test_provider_factory.py` patches `providers.LLM_PROVIDER` with `monkeypatch.setattr` — that name is bound into the module at import time, so setting the env var alone would not work.
- `tests/test_extractor_pipeline.py` stubs `extractor.get_llm_provider` and `extractor.extract_document_text`, so no network and no Tesseract.
- `tests/test_result_tables.py` covers labels, dtypes and export payloads with no Streamlit runtime. `tests/test_app_render.py` imports `app` (safe: `st.stop()` is a no-op in bare mode) and calls `app.render_results` with `st.dataframe`/`st.download_button`/`st.columns` monkeypatched, asserting the wiring rather than pixels.
- AppTest can only reach the results UI by seeding `session_state["analysis"]` before `.run()`; there is no public API for setting a `file_uploader`, and results render from session state rather than from the button handler. The download *payload* is not readable from the proto (Streamlit keeps it in a file manager), so assert on `result_tables.to_csv_bytes`/`to_json_bytes` instead — that's what `test_app_render.py` does for filenames and mime types.

Then the manual levels, cheapest first:

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

- **`.gitignore` exists** and covers `.env`, `.streamlit/secrets.toml`, `__pycache__/`, `*.pyc`, `venv/`, `.pytest_cache/`. No `.pyc` is tracked any more, so the old "modified .pyc on every run" diff noise is gone.
- **`config.py`, `providers/`, `tests/`, `pytest.ini` and `requirements-dev.txt` are still untracked** — the provider refactor has not been committed. `extractor.py`, `main.py`, `requirements.txt`, `README.md` and `AGENTS.md` all have uncommitted edits; don't assume `HEAD` reflects the working tree.
- `invoices-img/` **is** tracked (8 fixture files, ~binary). They're the only way to test extraction; don't delete them, and don't add more casually.
- `usecases/` ships 3 configs: `invoice` (3 fields), `sale_deed` (4 fields), `sales_order_ub` (12 fields + a 6-field `items` array — the only usecase that exercises the line-item table path).
- No CI, no `.streamlit/config.toml`, no packaging, no `src/` layout: root-level scripts only. `config.py` holds env-driven settings and nothing else. Business logic belongs in `extractor.py`; LLM backends belong in `providers/`; `main.py` and `app.py` are transport/UI only. New root modules should be plainly named.
