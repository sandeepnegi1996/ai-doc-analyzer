"""Tests for the batch domain model: DocumentResult, BatchResult, process_batch.

These run offline: analyze_document is monkeypatched, so no LLM, no OCR.
"""

import pytest

import extractor
from batch.processor import (
    ERROR_OCR,
    BatchResult,
    BatchStatus,
    DocumentResult,
    DocumentStatus,
    process_batch,
)

DOC_A = b"%PDF-1.4 fake A"
DOC_B = b"%PDF-1.4 fake B"
DOC_C = b"%PDF-1.4 fake C"


@pytest.fixture
def stub_analyze(monkeypatch):
    """Stub analyze_document with a function that succeeds for most docs."""
    calls = []

    def fake_analyze(file_bytes, filename, usecase):
        calls.append((file_bytes, filename, usecase))
        if filename == "bad.pdf":
            raise extractor.EmptyDocumentError("No readable text")
        return {
            "order_no": f"SO-{filename}",
            "company_name": "Test Co",
            "items": [{"item_code_sku": "SKU-1", "ordered_qty": 5}],
        }

    monkeypatch.setattr(extractor, "analyze_document", fake_analyze)
    return calls


# --- process_batch ---------------------------------------------------------


def test_process_batch_returns_batch_result(stub_analyze):
    batch = process_batch([(DOC_A, "order_001.pdf")], "sales_order_ub")
    assert isinstance(batch, BatchResult)
    assert batch.usecase == "sales_order_ub"
    assert len(batch.documents) == 1


def test_process_batch_preserves_upload_order(stub_analyze):
    docs = [(DOC_A, "a.pdf"), (DOC_B, "b.pdf"), (DOC_C, "c.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert [d.filename for d in batch.documents] == ["a.pdf", "b.pdf", "c.pdf"]


def test_process_batch_all_success(stub_analyze):
    docs = [(DOC_A, "a.pdf"), (DOC_B, "b.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert batch.status == BatchStatus.COMPLETED
    assert len(batch.succeeded) == 2
    assert len(batch.failed) == 0


def test_process_batch_partial_failure(stub_analyze):
    docs = [(DOC_A, "good.pdf"), (DOC_B, "bad.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert batch.status == BatchStatus.PARTIAL_SUCCESS
    assert len(batch.succeeded) == 1
    assert len(batch.failed) == 1


def test_process_batch_all_failed(stub_analyze):
    docs = [(DOC_A, "bad.pdf"), (DOC_B, "bad.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert batch.status == BatchStatus.FAILED
    assert len(batch.succeeded) == 0
    assert len(batch.failed) == 2


def test_process_batch_continues_after_failure(stub_analyze):
    """A failure in doc 1 must not prevent doc 2 from being processed."""
    docs = [(DOC_A, "bad.pdf"), (DOC_B, "good.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert batch.documents[0].status == "failed"
    assert batch.documents[1].status == DocumentStatus.COMPLETED


def test_process_batch_assigns_document_ids(stub_analyze):
    docs = [(DOC_A, "a.pdf"), (DOC_B, "b.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    ids = [d.document_id for d in batch.documents]
    assert all(id.startswith("doc-") for id in ids)
    assert len(set(ids)) == 2  # unique


def test_process_batch_computes_sha256(stub_analyze):
    batch = process_batch([(DOC_A, "a.pdf")], "sales_order_ub")
    doc = batch.documents[0]
    assert len(doc.sha256) == 64  # hex sha256
    assert doc.sha256 != ""


def test_process_batch_records_duration(stub_analyze):
    batch = process_batch([(DOC_A, "a.pdf")], "sales_order_ub")
    assert batch.documents[0].duration_seconds >= 0


def test_process_batch_stores_data_on_success(stub_analyze):
    batch = process_batch([(DOC_A, "a.pdf")], "sales_order_ub")
    doc = batch.documents[0]
    assert doc.data is not None
    assert doc.data["order_no"] == "SO-a.pdf"


def test_process_batch_stores_error_on_failure(stub_analyze):
    batch = process_batch([(DOC_A, "bad.pdf")], "sales_order_ub")
    doc = batch.documents[0]
    assert doc.data is None
    assert doc.error_code == ERROR_OCR
    assert doc.error_message is not None


def test_process_batch_generates_batch_id(stub_analyze):
    batch = process_batch([(DOC_A, "a.pdf")], "sales_order_ub")
    assert batch.batch_id.startswith("batch-")


def test_process_batch_empty_list():
    batch = process_batch([], "sales_order_ub")
    assert batch.status == BatchStatus.FAILED
    assert batch.documents == []


def test_process_batch_total_items(stub_analyze):
    docs = [(DOC_A, "a.pdf"), (DOC_B, "b.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    # Each doc has 1 item in the stub
    assert batch.total_items == 2


def test_process_batch_total_items_with_failure(stub_analyze):
    docs = [(DOC_A, "good.pdf"), (DOC_B, "bad.pdf")]
    batch = process_batch(docs, "sales_order_ub")
    assert batch.total_items == 1


# --- BatchResult status derivation ----------------------------------------


def test_batch_status_empty():
    batch = BatchResult(batch_id="b", usecase="invoice", documents=[])
    assert batch.status == BatchStatus.FAILED


def test_batch_status_all_completed():
    docs = [
        DocumentResult(document_id="d1", filename="a.pdf", status=DocumentStatus.COMPLETED, data={}),
        DocumentResult(document_id="d2", filename="b.pdf", status=DocumentStatus.COMPLETED, data={}),
    ]
    batch = BatchResult(batch_id="b", usecase="invoice", documents=docs)
    assert batch.status == BatchStatus.COMPLETED


def test_batch_status_all_failed():
    docs = [
        DocumentResult(document_id="d1", filename="a.pdf", status="failed"),
        DocumentResult(document_id="d2", filename="b.pdf", status="failed"),
    ]
    batch = BatchResult(batch_id="b", usecase="invoice", documents=docs)
    assert batch.status == BatchStatus.FAILED


def test_batch_status_mixed():
    docs = [
        DocumentResult(document_id="d1", filename="a.pdf", status=DocumentStatus.COMPLETED, data={}),
        DocumentResult(document_id="d2", filename="b.pdf", status="failed"),
    ]
    batch = BatchResult(batch_id="b", usecase="invoice", documents=docs)
    assert batch.status == BatchStatus.PARTIAL_SUCCESS
