import subprocess
import tempfile
import os
import json
import re
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form, HTTPException

app = FastAPI()
USECASES_DIR = Path(__file__).resolve().parent / "usecases"


def load_usecase(usecase):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", usecase):
        raise HTTPException(status_code=404, detail="Unknown document type")

    config_path = USECASES_DIR / f"{usecase}.json"
    if not config_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown document type")

    with config_path.open(encoding="utf-8") as config_file:
        return json.load(config_file)


@app.get("/usecases")
async def get_usecases():
    return [
        {"key": path.stem, "name": load_usecase(path.stem).get("name", path.stem)}
        for path in sorted(USECASES_DIR.glob("*.json"))
    ]

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

def extract_fields(text, config):
    fields = {}
    for field in config.get("fields", []):
        value = None
        for pattern in field.get("patterns", []):
            match = re.search(pattern, text)
            if match:
                value = match.group(1)
                if field.get("type") == "float":
                    value = float(value.replace(",", ""))
                break
        fields[field["key"]] = value

    fields["raw_text_preview"] = text
    return fields


@app.post("/analyze")
async def analyze(usecase: str = Form(...), file: UploadFile = File(...)):
    config = load_usecase(usecase)
    content = await file.read()
    if (file.filename or "").lower().endswith(".pdf"):
        text = extract_text_from_pdf(content)
    else:
        text = extract_text_from_image(content)
    return extract_fields(text, config)