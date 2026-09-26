import subprocess
import tempfile
import os
import io
import re
import fitz  # PyMuPDF
from PIL import Image
from fastapi import FastAPI, UploadFile, File

app = FastAPI()   # <-- THIS LINE MUST EXIST AT TOP LEVEL

def extract_text_from_pdf(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text

def extract_text_from_image(image_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["tesseract", tmp_path, "stdout"],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        return result.stdout
    finally:
        os.unlink(tmp_path)

def parse_invoice_fields(text):
    invoice_no = re.search(r"Invoice\s*#?\s*:?\s*(\w+)", text, re.I)
    total = re.search(r"Total\s*:?\s*\$?([\d,.]+)", text, re.I)
    return {
        "invoice_number": invoice_no.group(1) if invoice_no else None,
        "total": total.group(1) if total else None,
        "raw_text_preview": text
    }

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    content = await file.read()
    if file.filename.lower().endswith(".pdf"):
        text = extract_text_from_pdf(content)
    else:
        text = extract_text_from_image(content)
    return parse_invoice_fields(text)