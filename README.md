# Doc Analyzer MVP

Document analyzer that extracts structured data (invoice number, total, etc.) from PDFs and images using an LLM backend. The UI calls the extractor **in-process** — there is no HTTP layer between them.

## Stack
- **Streamlit** — the whole UI; talks to `extractor.py` directly
- **PyMuPDF** — PDF text extraction
- **Tesseract OCR** — image text extraction (via CLI)
- **Groq API** (`openai/gpt-oss-120b`) — LLM-based field extraction with strict JSON schema output
- **FastAPI** (`main.py`) — optional standalone API, not used by the UI

## Prerequisites
- Python 3.10+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) on PATH (`tesseract --version` should work) — locally, or on Streamlit Cloud via `packages.txt`
- `GROQ_API_KEY` — see [Configuration](#configuration)

## Configuration

The key is resolved in this order, and is **never committed**:

| Source | Used for | Committed? |
|---|---|---|
| `.env` in the project root | local dev | no — gitignored |
| `.streamlit/secrets.toml` | local dev, secrets path | no — gitignored |
| Streamlit Cloud → Tools → Secrets | deployment | no — set in the Cloud UI |
| A real `GROQ_API_KEY` env var | CI, containers | no |

`app.py` reads `os.getenv("GROQ_API_KEY")` first, then falls back to `st.secrets`, and copies
the result into `os.environ` so `extractor.get_client()` can see it. With nothing configured the
app shows a warning banner instead of failing.

Templates to copy, both placeholders only:
- `.env.example` → `.env` (local)
- `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` (local secrets path)

## Setup
```bash
git clone <your-repo>
cd doc-analyzer
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env         # then paste your key
```

## Run

One terminal. No backend needed:
```bash
GROQ_API_KEY=<your-key> streamlit run app.py
```
Open: http://localhost:8501

The optional FastAPI shell, if you want it (the UI does not):
```bash
uvicorn main:app --reload        # http://127.0.0.1:8000/docs
```

## Deploy to Streamlit Cloud

1. Push the repo to GitHub.
2. **New app** → pick the repo → branch → main file `app.py`.
3. **Tools → Secrets** → add `GROQ_API_KEY`.

Streamlit Cloud installs `requirements.txt` (Python) and `packages.txt` (system) at build time, so `tesseract-ocr` is available without any manual setup. Secrets are copied into the process env by `app.py` before extraction, since Cloud does not export them as env vars.

To change the model, edit `MODEL` in `extractor.py`.

## Usage
1. Select a document type and upload a supported PDF or image (`pdf`, `png`, `jpg`, `jpeg`, `tiff`, `bmp`).
2. Click **Analyze**.
3. View the configured fields and raw text preview. Document types with line items render as a
   per-item table instead of a single row.

## Current Capabilities

The extractor uses an LLM (not regex) to understand document semantics and return structured JSON.

| Capability | How it works |
|---|---|
| **Multi-format input** | PDF text via PyMuPDF; images OCR'd via Tesseract CLI |
| **Per-document-type fields** | Drop a JSON file in `usecases/` — the dropdown picks it up via `extractor.list_usecases()` |
| **Structured LLM output** | Groq `response_format` with strict JSON schema built from the usecase's `fields` |
| **Line items** | An optional `items` array per document type is returned as a JSON array and rendered as a table |
| **Type coercion** | `float`, `int`, and `date` fields are post-processed; best-effort with fallback to raw value |
| **Text truncation** | Document text truncated to first 6000 chars in the prompt (long docs partially extracted by design) |
| **Error resilience** | Unparseable LLM JSON → all fields `null`; missing API key → error at call time |

### Shipped document types

| Key | Display name | Fields | Line items |
|---|---|---|---|
| `invoice` | Invoice | `invoice_number`, `total`, `date` | no |
| `sales_order_ub` | Sales Order UB | 12 header fields (`order_no`, `company_name`, `contact_person`, `delivery_address`, …) | yes — `item_code_sku`, `item_name`, `ordered_qty`, `pack_size`, … |
| `sale_deed` | Sale Deed | `property_size`, `seller_name`, `buyer_name`, `registration_date` | no |

### Usecase config shape

```json
{
  "name": "Display Name",
  "fields": [
    { "key": "field_name", "type": "string|float|int|date", "description": "..." }
  ]
}
```

- Filename stem must match `[A-Za-z0-9_-]+`; it becomes the dropdown's usecase key (`sale_deed` uses an underscore).
- `name`, `key`, `type`, `description` are consumed by the code; `patterns` is **dead config** (present in the shipped JSON, read by nothing).
- Supported types: `string`, `float`, `int`, `date` (date is a `YYYY-MM-DD` string).
- All fields are emitted as `required` with `[type, "null"]` in the schema — absent values come back `null`.
- Optional `"items"` array (same field shape) is returned as a JSON array under `"items"`.
- Don't name a field `raw_text_preview`; the extractor overwrites that key with the document text.

## Project Structure
```
app.py                          # Streamlit UI (calls analyze_document directly)
extractor.py                    # all extraction logic; no HTTP, no Streamlit
main.py                         # optional FastAPI shell over extractor.py
requirements.txt                # Python dependencies
packages.txt                    # system packages for Streamlit Cloud (tesseract-ocr)
usecases/*.json                 # per-document-type field definitions
invoices-img/                   # sample documents for manual testing
.env.example                    # key template (copy to .env)
.streamlit/secrets.toml.example # secrets template (copy to secrets.toml)
```

## Verify

No test suite — verification is manual. A known-good end-to-end check from the project root:

```bash
python -c "import extractor; r = extractor.analyze_document(open('invoices-img/5.png','rb').read(), '5.png', 'invoice'); print(r['invoice_number'], r['total'], r['date'])"
# INV-10012 1699.48 2021-03-26
```

## Roadmap
- [ ] Preprocess images for better OCR accuracy
- [ ] OCR fallback for scanned PDFs
- [ ] Local LLM fallback (Ollama) for offline use

## Notes
- Add a JSON config under `usecases/` to expose another document type in the UI.
- **Document text is sent to Groq's API** for extraction — not fully local, not zero-cost (though `gpt-oss-120b` is currently free on Groq).
- Scanned PDFs have no text layer and no OCR fallback, so they extract as all-null.
- Add a new numeric field type in two places: `type_map` in `build_json_schema` and the branch in `coerce_types`.
