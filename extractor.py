"""Document extraction business logic.

Pure business logic: bytes in, JSON-ready dict out. No HTTP, no FastAPI.
Callers (FastAPI shell, Streamlit UI, scripts) handle transport and error
mapping; this module only raises ExtractorError subclasses.

The LLM call is delegated to an LLMProvider abstraction obtained from
`providers.get_llm_provider()`. This module builds the prompt and the JSON
schema, but knows nothing about Groq, Ollama or OpenAI -- the provider name and
model come from config.py. Swapping backends is a config change, not a code
change.

    bytes -> text (PDF/OCR) -> build_prompt -> build_json_schema
          -> LLMProvider.extract -> parse_llm_response -> coerce_types
          -> validate_result -> dict

The pipeline is deliberately provider-independent: the prompt and the schema are
built here, and a provider only wraps the schema in whatever its API expects.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from dotenv import load_dotenv

from providers import get_llm_provider

# The provider error classes are aliased because this module defines its own
# transport-facing counterparts below. Importing them under the bare names
# would be shadowed by those class definitions, and the `except` clauses in
# _acquire_provider would silently stop matching.
from providers import EmptyResponseError as ProviderEmptyResponseError
from providers import MissingApiKeyError as ProviderMissingApiKeyError
from providers import ModelMismatchError as ProviderModelMismatchError
from providers import UnsupportedProviderError as ProviderUnsupportedError

ROOT_DIR = Path(__file__).resolve().parent
USECASES_DIR = ROOT_DIR / "usecases"
load_dotenv(ROOT_DIR / ".env")  # no-op if absent; real env vars win

MAX_DOC_CHARS = 6000
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
RAW_TEXT_KEY = "raw_text_preview"
MAX_TOKENS = 2000
TEMPERATURE = 0


class ExtractorError(Exception):
    """Base class for extraction failures. Callers map these to transport errors."""


class UnknownUsecaseError(ExtractorError):
    """Usecase key missing or not a safe filename."""


class MissingApiKeyError(ExtractorError):
    """The selected LLM provider has no API key configured."""


class UnsupportedProviderError(ExtractorError):
    """LLM_PROVIDER names a backend this app does not know about."""


class UnsupportedFileError(ExtractorError):
    """File cannot be read as text by the available backends."""

    DEFAULT_MESSAGE = "Unsupported file type. Please upload PDF, PNG, JPG or JPEG."


class EmptyDocumentError(ExtractorError):
    """No readable text found in the document."""


class OversizedDocumentError(ExtractorError):
    """Document exceeds the maximum allowed file size."""


class LLMFailureError(ExtractorError):
    """The language model call failed."""


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
    """The bare JSON Schema object describing the extraction result.

    Deliberately provider-agnostic: this is the schema and nothing else. Each
    provider wraps it in whatever envelope its API expects, so no Groq-shaped
    `response_format` key leaks into this module.
    """
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
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
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
        if not isinstance(item, dict):
            continue  # a schema-less model can return junk; validate_result drops it
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


# --- parsing / validation --------------------------------------------------


def parse_llm_response(raw, config):
    """The provider's raw response string as a field dict. Never raises.

    A malformed response, or valid JSON that is not an object (`[1, 2]`, a bare
    string), degrades to an all-null dict: a partial answer beats a 503, and the
    caller can see exactly which fields came back empty.
    """
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):  # ValueError covers json.JSONDecodeError
        parsed = None
    if not isinstance(parsed, dict):
        return {f["key"]: None for f in config.get("fields", [])}
    return parsed


def validate_result(result, config):
    """Reshape the model's dict into exactly the keys the usecase declares.

    Key-level normalisation only: every declared field is present (missing ones
    become None), `items` is a list of dicts carrying the declared item keys,
    and keys the usecase does not declare are dropped. Types are
    `coerce_types`' business and are deliberately not second-guessed here, so a
    value that refused to coerce survives as the model's raw value instead of
    being nulled here.
    """
    validated = {f["key"]: result.get(f["key"]) for f in config.get("fields", [])}
    if config.get("items"):
        raw_items = result.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        item_keys = [f["key"] for f in config["items"]]
        rows = [row for row in raw_items if isinstance(row, dict)]
        validated["items"] = [{key: row.get(key) for key in item_keys} for row in rows]
    return validated


# --- extraction -----------------------------------------------------------


def _acquire_provider():
    """Build the configured LLMProvider, mapping its errors onto ours.

    This is the only place provider-layer exceptions are translated, which is
    what lets the rest of extractor.py (and every transport in front of it)
    stay provider-agnostic.
    """
    try:
        return get_llm_provider()
    except ProviderMissingApiKeyError as error:
        raise MissingApiKeyError(
            "Configuration error. Please contact the administrator."
        ) from error
    except (ProviderUnsupportedError, ProviderModelMismatchError) as error:
        raise UnsupportedProviderError(str(error)) from error


def call_llm(provider, text, config):
    """Ask the provider for the fields; return its raw response string.

    The schema is only sent when the provider advertises schema support.
    Otherwise the call falls back to the API's plain JSON mode plus the
    prompt's own "return only JSON" instruction -- which is what
    `ProviderCapabilities` is for: ask, instead of finding out from a 400.
    """
    schema = build_json_schema(config) if provider.capabilities.json_schema else None
    try:
        return provider.extract(
            prompt=build_prompt(text, config),
            json_schema=schema,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
    except ProviderEmptyResponseError as error:
        raise LLMFailureError("Document analysis failed. Please try again.") from error


def extract_fields(text, usecase):
    """Extract one already-parsed document: text -> LLMProvider -> field dict."""
    config = load_usecase(usecase)
    raw = call_llm(_acquire_provider(), text, config)
    fields = coerce_types(parse_llm_response(raw, config), config)
    fields = validate_result(fields, config)
    fields[RAW_TEXT_KEY] = text
    return fields


def analyze_document(file_bytes, filename, usecase):
    """Full pipeline: bytes -> text (PDF/OCR) -> LLMProvider -> field dict.

        1. usecase config   (loaded first, to fail before any OCR or LLM work)
        2. document text    (PDF text layer, or Tesseract OCR)
        3. prompt + schema  (built here, provider-independent)
        4. provider call    (connect / send / receive / return, nothing else)
        5. parse, coerce, validate
    """
    config = load_usecase(usecase)  # fail fast on an unknown usecase, before any work
    text = extract_document_text(file_bytes, filename)
    try:
        return extract_fields(text, config)
    except ExtractorError:
        raise
    except Exception as error:
        raise LLMFailureError("Document analysis failed. Please try again.") from error
