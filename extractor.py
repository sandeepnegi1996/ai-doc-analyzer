"""Document extraction business logic.

Pure business logic: bytes in, JSON-ready dict out. No HTTP, no FastAPI.
Callers (FastAPI shell, Streamlit UI, scripts) handle transport and error
mapping; this module only raises ExtractorError subclasses.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from dotenv import load_dotenv
from groq import Groq

ROOT_DIR = Path(__file__).resolve().parent
USECASES_DIR = ROOT_DIR / "usecases"
load_dotenv(ROOT_DIR / ".env")  # no-op if absent; real env vars win

MODEL = "openai/gpt-oss-120b"  # free on Groq, supports strict JSON schema mode
MAX_DOC_CHARS = 6000
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
RAW_TEXT_KEY = "raw_text_preview"

_client = None


class ExtractorError(Exception):
    """Base class for extraction failures. Callers map these to transport errors."""


class UnknownUsecaseError(ExtractorError):
    """Usecase key missing or not a safe filename."""


class MissingApiKeyError(ExtractorError):
    """GROQ_API_KEY is not configured."""


class UnsupportedFileError(ExtractorError):
    """File cannot be read as text by the available backends."""

    DEFAULT_MESSAGE = "Unsupported file type. Please upload PDF, PNG, JPG or JPEG."


class EmptyDocumentError(ExtractorError):
    """No readable text found in the document."""


class OversizedDocumentError(ExtractorError):
    """Document exceeds the maximum allowed file size."""


class LLMFailureError(ExtractorError):
    """The language model call failed."""


def get_client():
    """Build the Groq client on first use so a missing key fails the call, not the import."""
    global _client
    if _client is None:
        if not os.environ.get("GROQ_API_KEY"):
            raise MissingApiKeyError(
                "Configuration error. Please contact the administrator."
            )
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


# --- usecase config -------------------------------------------------------


def load_usecase(usecase):
    """Load a usecase config by key, or return it unchanged if already loaded."""
    if isinstance(usecase, dict):
        return usecase
    if not isinstance(usecase, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", usecase):
        raise UnknownUsecaseError("Unknown document type")
    config_path = USECASES_DIR / f"{usecase}.json"
    if not config_path.is_file():
        raise UnknownUsecaseError("Unknown document type")
    with config_path.open(encoding="utf-8") as config_file:
        return json.load(config_file)


def list_usecases():
    """Every usecase on disk, sorted by filename stem."""
    return [
        {"key": path.stem, "name": load_usecase(path.stem).get("name", path.stem)}
        for path in sorted(USECASES_DIR.glob("*.json"))
    ]


# --- text extraction ------------------------------------------------------


def extract_text_from_image(file_bytes):
    """OCR an image with Tesseract. Tesseract sniffs content, so the suffix is cosmetic."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        try:
            result = subprocess.run(
                ["tesseract", tmp_path, "stdout"],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError as error:
            raise UnsupportedFileError(
                UnsupportedFileError.DEFAULT_MESSAGE
            ) from error
        text = result.stdout.strip()
        if not text:
            raise EmptyDocumentError(
                "No readable text was found in this document."
            )
        return text
    finally:
        os.unlink(tmp_path)


def extract_text_from_pdf(file_bytes):
    """Concatenate the text layer of a PDF. No OCR fallback: scans yield ''."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as error:
        raise UnsupportedFileError(
            UnsupportedFileError.DEFAULT_MESSAGE
        ) from error
    try:
        text = "".join(page.get_text() for page in doc)
        if not text.strip():
            raise EmptyDocumentError(
                "No readable text was found in this document."
            )
        return text
    finally:
        doc.close()


def extract_document_text(file_bytes, filename):
    """Route by extension: PDF goes to PyMuPDF, everything else to OCR."""
    if len(file_bytes) > MAX_FILE_SIZE:
        raise OversizedDocumentError("Oversized document")
    if not file_bytes:
        raise EmptyDocumentError("No readable text was found in this document.")
    ext = (filename or "").lower()
    if ext.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    if ext.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
        return extract_text_from_image(file_bytes)
    raise UnsupportedFileError(UnsupportedFileError.DEFAULT_MESSAGE)


# --- prompt / schema ------------------------------------------------------


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
{text[:MAX_DOC_CHARS]}
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


# --- extraction -----------------------------------------------------------


def extract_fields(text, usecase):
    """Send document text to Groq and return coerced fields plus the raw text."""
    config = load_usecase(usecase)
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
    fields[RAW_TEXT_KEY] = text
    return fields


def analyze_document(file_bytes, filename, usecase):
    """Full pipeline: bytes -> text (PDF/OCR) -> Groq -> field dict."""
    config = load_usecase(usecase)  # fail fast on an unknown usecase, before any work
    text = extract_document_text(file_bytes, filename)
    try:
        return extract_fields(text, config)
    except ExtractorError:
        raise
    except Exception as error:
        raise LLMFailureError("Document analysis failed. Please try again.") from error
