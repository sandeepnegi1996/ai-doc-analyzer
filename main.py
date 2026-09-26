import subprocess
import tempfile
import os
import json
import re
from pathlib import Path
import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from groq import Groq

app = FastAPI()
ROOT_DIR = Path(__file__).resolve().parent
USECASES_DIR = ROOT_DIR / "usecases"
load_dotenv(ROOT_DIR / ".env")  # no-op if absent; real env vars win
MODEL = "openai/gpt-oss-120b"  # free on Groq, supports strict JSON schema mode

_client = None


def get_client():
    """Build the Groq client on first use so a missing key fails the request, not the import."""
    global _client
    if _client is None:
        if not os.environ.get("GROQ_API_KEY"):
            raise HTTPException(
                status_code=500,
                detail=(
                    "GROQ_API_KEY is not set. Add it to .env in the project root or "
                    "export it before starting the server."
                ),
            )
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


def load_usecase(usecase):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", usecase):
        raise HTTPException(status_code=404, detail="Unknown document type")

    config_path = USECASES_DIR / f"{usecase}.json"
    if not config_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown document type")

    with config_path.open(encoding="utf-8") as config_file:
        return json.load(config_file)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "groq",
            }
        ],
    }


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
            encoding="utf-8",
        )
        return result.stdout
    finally:
        os.unlink(tmp_path)


def build_prompt(text, config):
    field_lines = "\n".join(
        f'- "{f["key"]}" ({f.get("type", "string")}): {f.get("description", f["key"])}'
        for f in config.get("fields", [])
    )
    items_section = ""
    if config.get("items"):
        item_lines = "\n".join(
            f'  - "{f["key"]}" ({f.get("type", "string")}): {f.get("description", f["key"])}'
            for f in config["items"]
        )
        items_section = f"""
Items (return as an array under "items", one entry per item line in the order):
{item_lines}
"""
    return f"""Extract the following fields from the document text below.
Return ONLY a single JSON object with exactly these keys — no other text, no markdown fences.
Use null for any field you cannot find. Return "float" fields as plain numbers (no
currency symbols or commas). Return "date" fields as YYYY-MM-DD.

Fields to extract:
{field_lines}
{items_section}
Document text:
\"\"\"
{text[:6000]}
\"\"\"

JSON:"""


def build_json_schema(config):
    type_map = {"string": "string", "float": "number", "int": "integer", "date": "string"}
    properties = {
        f["key"]: {"type": [type_map.get(f.get("type", "string"), "string"), "null"]}
        for f in config.get("fields", [])
    }
    required = list(properties.keys())
    if config.get("items"):
        item_properties = {
            f["key"]: {"type": [type_map.get(f.get("type", "string"), "string"), "null"]}
            for f in config["items"]
        }
        properties["items"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": item_properties,
                "required": list(item_properties.keys()),
                "additionalProperties": False,
            },
        }
        required.append("items")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "extracted_fields",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def coerce_types(fields, config):
    type_map = {"float": lambda v: float(str(v).replace(",", "").replace("$", "")),
                "int": lambda v: int(str(v).replace(",", ""))}
    for f in config.get("fields", []):
        key, ftype = f["key"], f.get("type")
        val = fields.get(key)
        if val is None or ftype not in type_map:
            continue
        try:
            fields[key] = type_map[ftype](val)
        except (ValueError, TypeError):
            pass  # leave as-is if coercion fails, better than dropping the value
    for item in fields.get("items") or []:
        for f in config.get("items", []):
            key, ftype = f["key"], f.get("type")
            val = item.get(key)
            if val is None or ftype not in type_map:
                continue
            try:
                item[key] = type_map[ftype](val)
            except (ValueError, TypeError):
                pass
    return fields


def extract_fields(text, config):
    response = get_client().chat.completions.create(
        model=MODEL,
        max_tokens=2000,
        temperature=0,
        response_format=build_json_schema(config),
        messages=[{"role": "user", "content": build_prompt(text, config)}],
    )
    raw = response.choices[0].message.content.strip()
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError:
        fields = {f["key"]: None for f in config.get("fields", [])}
    fields = coerce_types(fields, config)
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