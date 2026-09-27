"""The extraction pipeline, with no network and no OCR.

Covers the stages `analyze_document` runs and, above all, the boundary between
it and the provider: the prompt and the bare JSON Schema are built here, and the
provider only ever sees a finished prompt.
"""

import json

import pytest
from conftest import StubProvider

import extractor
from extractor import (
    LLMFailureError,
    UnknownUsecaseError,
    analyze_document,
    build_json_schema,
    build_prompt,
    call_llm,
    coerce_types,
    load_usecase,
    parse_llm_response,
    validate_result,
)

INVOICE = load_usecase("invoice")
ORDER = load_usecase("sales_order_ub")
DOCUMENT_TEXT = "INVOICE\nInvoice Number: INV-10012\nTotal: 1699.48\nDate: 2021-03-26"


@pytest.fixture
def provider(monkeypatch):
    """Install a stub provider so the pipeline runs with no SDK and no API."""
    stub = StubProvider(
        '{"invoice_number": "INV-10012", "total": "1699.48", "date": "2021-03-26"}'
    )
    monkeypatch.setattr(extractor, "get_llm_provider", lambda: stub)
    return stub


@pytest.fixture
def no_ocr(monkeypatch):
    """Skip PDF/OCR: these tests are about the LLM stages, not the bytes."""
    monkeypatch.setattr(
        extractor, "extract_document_text", lambda file_bytes, filename: DOCUMENT_TEXT
    )


# --- prompt and schema -----------------------------------------------------


def test_schema_is_bare_so_a_provider_never_double_wraps_it():
    """The Groq-shaped envelope lives in providers/, not in the schema."""
    schema = build_json_schema(INVOICE)
    assert set(schema) == {"type", "properties", "required", "additionalProperties"}
    assert "response_format" not in json.dumps(schema)
    assert schema["properties"]["total"]["type"] == ["number", "null"]
    assert schema["required"] == ["invoice_number", "total", "date"]


def test_schema_makes_line_items_a_required_array():
    schema = build_json_schema(ORDER)
    assert schema["properties"]["items"]["type"] == "array"
    assert "items" in schema["required"]


def test_prompt_asks_for_one_json_object_and_truncates():
    config = dict(INVOICE, fields=[{"key": "body", "description": "the body"}])
    prompt = build_prompt("x" * (extractor.MAX_DOC_CHARS + 500), config)
    assert "ONLY a single JSON object" in prompt
    assert "x" * (extractor.MAX_DOC_CHARS + 1) not in prompt  # truncated
    assert "items" not in prompt  # this usecase declares none


# --- parsing ---------------------------------------------------------------


def test_parse_accepts_an_object():
    assert parse_llm_response('{"total": "10"}', INVOICE) == {"total": "10"}


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "",
        "[1, 2, 3]",       # valid JSON, wrong shape
        '"a string"',      # valid JSON, wrong shape
        "null",
        None,
    ],
)
def test_parse_degrades_to_all_null_instead_of_raising(raw):
    parsed = parse_llm_response(raw, INVOICE)
    assert set(parsed) == {"invoice_number", "total", "date"}
    assert all(value is None for value in parsed.values())


# --- coercion --------------------------------------------------------------


def test_coerce_numbers_and_leave_the_rest():
    fields = coerce_types(
        {"invoice_number": "INV-1", "total": "$1,699.48", "date": "2021-03-26"}, INVOICE
    )
    assert fields["total"] == 1699.48
    assert fields["date"] == "2021-03-26"  # a date is just a string


def test_coercion_keeps_an_uncoercible_value():
    fields = coerce_types({"total": "about two hundred"}, INVOICE)
    assert fields["total"] == "about two hundred"


def test_coercion_reaches_line_items():
    config = {"fields": [], "items": [{"key": "ordered_qty", "type": "int"}]}
    fields = coerce_types({"items": [{"ordered_qty": "1,200"}]}, config)
    assert fields["items"][0]["ordered_qty"] == 1200


def test_coercion_survives_junk_line_items():
    config = {"fields": [], "items": [{"key": "ordered_qty", "type": "int"}]}
    fields = coerce_types({"items": ["oops", {"ordered_qty": "2"}]}, config)
    assert fields["items"][1]["ordered_qty"] == 2


# --- validation ------------------------------------------------------------


def test_validate_fills_missing_fields_with_null():
    validated = validate_result({"invoice_number": "INV-1"}, INVOICE)
    assert validated == {"invoice_number": "INV-1", "total": None, "date": None}


def test_validate_drops_keys_the_usecase_does_not_declare():
    validated = validate_result({"total": 1, "hallucinated": "x"}, INVOICE)
    assert "hallucinated" not in validated


def test_validate_normalizes_line_items_to_declared_keys():
    validated = validate_result(
        {"items": [{"item_name": "Tea", "extra": 1}, "junk"]}, ORDER
    )
    assert validated["items"] == [
        {
            "item_code_sku": None,
            "item_name": "Tea",
            "items_category": None,
            "pack_size": None,
            "ordered_qty": None,
            "brand_name": None,
        }
    ]


def test_validate_turns_a_missing_items_array_into_an_empty_list():
    assert validate_result({}, ORDER)["items"] == []


def test_validate_leaves_uncoerced_values_alone():
    """Second-guessing a type here would null a value coerce_types kept."""
    validated = validate_result({"total": "about two hundred"}, INVOICE)
    assert validated["total"] == "about two hundred"


# --- the provider call -----------------------------------------------------


def test_call_sends_the_bare_schema_when_the_provider_supports_one():
    stub = StubProvider("{}")
    call_llm(stub, DOCUMENT_TEXT, INVOICE)
    assert stub.requests[0]["json_schema"] == build_json_schema(INVOICE)
    assert DOCUMENT_TEXT in stub.requests[0]["prompt"]
    assert stub.requests[0]["temperature"] == extractor.TEMPERATURE


def test_call_drops_the_schema_when_the_provider_cannot_honour_one():
    stub = StubProvider("{}", json_schema_supported=False)
    call_llm(stub, DOCUMENT_TEXT, INVOICE)
    assert stub.requests[0]["json_schema"] is None
    assert DOCUMENT_TEXT in stub.requests[0]["prompt"]  # the text still goes over


def test_call_turns_an_empty_provider_response_into_a_failure():
    from providers.base import EmptyResponseError

    class EmptyProvider(StubProvider):
        def extract(self, *args, **kwargs):
            raise EmptyResponseError("nothing came back")

    with pytest.raises(LLMFailureError):
        call_llm(EmptyProvider(), DOCUMENT_TEXT, INVOICE)


# --- the whole pipeline ----------------------------------------------------


def test_analyze_document_end_to_end(provider, no_ocr):
    result = analyze_document(b"%PDF-1.4 fake", "invoice.pdf", "invoice")
    assert result["invoice_number"] == "INV-10012"
    assert result["total"] == 1699.48          # coerced to float
    assert result["date"] == "2021-03-26"
    assert result["raw_text_preview"] == DOCUMENT_TEXT
    assert list(result) == ["invoice_number", "total", "date", "raw_text_preview"]


def test_analyze_document_survives_a_malformed_response(provider, no_ocr):
    provider.response = "I could not read that document."
    result = analyze_document(b"bytes", "scan.png", "invoice")
    assert result["invoice_number"] is None
    assert result["raw_text_preview"] == DOCUMENT_TEXT


def test_analyze_document_reports_an_unknown_usecase_before_any_ocr(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("OCR should never run for an unknown usecase")

    monkeypatch.setattr(extractor, "extract_document_text", fail)
    with pytest.raises(UnknownUsecaseError):
        analyze_document(b"bytes", "scan.png", "does_not_exist")
