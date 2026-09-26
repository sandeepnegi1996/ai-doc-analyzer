# Doc Analyzer MVP

Local document analyzer that extracts structured data (invoice number, total, etc.) from PDFs and images. Runs entirely on your machine — zero API cost.

## Stack
- **FastAPI** — backend API
- **Streamlit** — frontend UI
- **PyMuPDF** — PDF text extraction
- **Tesseract OCR** — image text extraction (via CLI, no pytesseract)

## Prerequisites
- Python 3.10+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed and on PATH (`tesseract --version` should work)

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
uvicorn main:app --reload
```
Check it works: http://127.0.0.1:8000/docs

**Terminal 2 — Frontend:**
```bash
streamlit run app.py
```
Open: http://localhost:8501

## Usage
1. Upload a PDF, PNG, or JPG invoice.
2. Click **Analyze**.
3. View extracted JSON (invoice number, total, raw text preview).

## Project Structure
```
main.py          # FastAPI backend + extraction logic
app.py           # Streamlit UI
requirements.txt # Dependencies
```

## Roadmap
- [ ] Preprocess images for better OCR accuracy
- [ ] Pydantic schemas for typed output
- [ ] Per-document-type regex rules (`rules.json`)
- [ ] Local LLM fallback (Ollama) for messy documents
- [ ] Docker packaging

## Notes
- Currently tuned for **invoices only**. Add more document types as needed.
- All processing is local — no data leaves your machine.