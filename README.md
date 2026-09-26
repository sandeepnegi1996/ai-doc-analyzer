# Doc Analyzer MVP

Local document analyzer that extracts structured data (invoice number, total, etc.) from PDFs and images using an LLM backend.

## Stack
- **FastAPI** — backend API
- **Streamlit** — frontend UI
- **PyMuPDF** — PDF text extraction
- **Tesseract OCR** — image text extraction (via CLI)
- **Groq API** (`openai/gpt-oss-120b`) — LLM-based field extraction with strict JSON schema output

## Prerequisites
- Python 3.10+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed and on PATH (`tesseract --version` should work)
- `GROQ_API_KEY` environment variable set (or in `.env` at project root)

## Setup
```bash
git clone <your-repo>
cd doc-analyzer-mvp
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

## Run

**Terminal 1 — Backend:**
```bash
GROQ_API_KEY=<your-key> uvicorn main:app --reload
```
Check it works: http://127.0.0.1:8000/docs

**Terminal 2 — Frontend:**
```bash
streamlit run app.py
```
Open: http://localhost:8501

## Usage
1. Select a document type and upload a supported PDF or image.
2. Click **Analyze**.
3. View the configured fields and raw text preview.

## Current Capabilities (Level 3)

The extractor uses an LLM (not regex) to understand document semantics and return structured JSON.

| Capability | How it works |
|---|---|
| **Multi-format input** | PDF text via PyMuPDF; images OCR'd via Tesseract CLI |
| **Per-document-type fields** | Drop a JSON file in `usecases/` — the dropdown picks it up via `GET /usecases` |
| **Structured LLM output** | Groq `response_format` with strict JSON schema built from the usecase's `fields` |
| **Type coercion** | `float`, `int`, and `date` fields are post-processed; best-effort with fallback to raw value |
| **Text truncation** | Document text truncated to first 6000 chars in the prompt (long docs partially extracted by design) |
| **Error resilience** | Unparseable LLM JSON → all fields `null`; missing API key → 500 at request time |

### Usecase config shape

```json
{
  "name": "Display Name",
  "fields": [
    { "key": "field_name", "type": "string|float|int|date", "description": "..." }
  ]
}
```

- `name`, `key`, `type`, `description` are consumed by the code; `patterns` is **dead config** (ignored).
- Supported types: `string`, `float`, `int`, `date` (date is a `YYYY-MM-DD` string).
- All fields are emitted as `required` with `[type, "null"]` in the schema — absent values come back `null`.

## Project Structure
```
main.py            # FastAPI backend + extraction logic
app.py             # Streamlit UI
requirements.txt   # Dependencies
usecases/*.json    # Per-document-type field definitions
```

## Roadmap
- [ ] Preprocess images for better OCR accuracy
- [ ] Pydantic schemas for typed output
- [ ] Local LLM fallback (Ollama) for offline use
- [ ] Docker packaging

## Notes
- Add a JSON config under `usecases/` to expose another document type in the frontend.
- **Document text is sent to Groq's API** for extraction — not fully local, not zero-cost (though `gpt-oss-120b` is currently free on Groq).
