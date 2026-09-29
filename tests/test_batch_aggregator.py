"""Tests for the batch aggregator: flatten_batch, batch_columns, batch_to_frame.

Offline and deterministic: no Streamlit runtime, no LLM, no fixtures.
"""

import pandas as pd
import pytest

from batch.aggregator import batch_columns, batch_to_frame, flatten_batch
from batch.processor import (
    BatchResult,
    DocumentResult,
    DocumentStatus,
)
from extractor import load_usecase

ORDER_CONFIG = load_usecase("sales_order_ub")
INVOICE_CONFIG = load_usecase("invoice")


def _doc(filename, data=None, status=DocumentStatus.COMPLETED):
    return DocumentResult(
        document_id=f"doc-{filename}",
        filename=filename,
        status=status,
        data=data,
        error_code=None if status == DocumentStatus.COMPLETED else "test_error",
        error_message=None if status == DocumentStatus.COMPLETED else "test error",
    )


def _batch(docs, usecase="sales_order_ub"):
    return BatchResult(batch_id="batch-test", usecase=usecase, documents=docs)


# --- flatten_batch ---------------------------------------------------------


def test_flatten_batch_one_doc_with_items():
    data = {
        "order_no": "SO-1001",
        "company_name": "ABC Ltd",
        "items": [
            {"item_code_sku": "SKU-101", "ordered_qty": 10},
            {"item_code_sku": "SKU-102", "ordered_qty": 20},
        ],
    }
    batch = _batch([_doc("order_001.pdf", data)])
    rows = flatten_batch(batch)
    assert len(rows) == 2
    assert rows[0]["source_file"] == "order_001.pdf"
    assert rows[0]["order_no"] == "SO-1001"
    assert rows[0]["item_code_sku"] == "SKU-101"
    assert rows[0]["ordered_qty"] == 10
    assert rows[1]["item_code_sku"] == "SKU-102"


def test_flatten_batch_multiple_docs():
    data_a = {
        "order_no": "SO-1001",
        "company_name": "ABC Ltd",
        "items": [{"item_code_sku": "SKU-101", "ordered_qty": 10}],
    }
    data_b = {
        "order_no": "SO-1002",
        "company_name": "XYZ Ltd",
        "items": [{"item_code_sku": "SKU-201", "ordered_qty": 5}],
    }
    batch = _batch([_doc("a.pdf", data_a), _doc("b.pdf", data_b)])
    rows = flatten_batch(batch)
    assert len(rows) == 2
    assert rows[0]["source_file"] == "a.pdf"
    assert rows[1]["source_file"] == "b.pdf"


def test_flatten_batch_preserves_upload_order():
    data_a = {"order_no": "A", "company_name": "A", "items": [{"item_code_sku": "A", "ordered_qty": 1}]}
    data_b = {"order_no": "B", "company_name": "B", "items": [{"item_code_sku": "B", "ordered_qty": 2}]}
    batch = _batch([_doc("z.pdf", data_a), _doc("a.pdf", data_b)])
    rows = flatten_batch(batch)
    assert rows[0]["order_no"] == "A"
    assert rows[1]["order_no"] == "B"


def test_flatten_batch_doc_with_no_items():
    data = {"order_no": "SO-1001", "company_name": "ABC Ltd", "items": []}
    batch = _batch([_doc("order.pdf", data)])
    rows = flatten_batch(batch)
    assert len(rows) == 1
    assert rows[0]["order_no"] == "SO-1001"
    assert rows[0]["item_code_sku"] is None
    assert rows[0]["ordered_qty"] is None


def test_flatten_batch_skips_failed_docs():
    data = {"order_no": "SO-1001", "company_name": "ABC", "items": [{"item_code_sku": "A", "ordered_qty": 1}]}
    batch = _batch([
        _doc("good.pdf", data),
        _doc("bad.pdf", status="failed"),
    ])
    rows = flatten_batch(batch)
    assert len(rows) == 1
    assert rows[0]["source_file"] == "good.pdf"


def test_flatten_batch_all_failed():
    batch = _batch([
        _doc("bad1.pdf", status="failed"),
        _doc("bad2.pdf", status="failed"),
    ])
    assert flatten_batch(batch) == []


def test_flatten_batch_missing_values_stay_null():
    data = {"order_no": "SO-1001", "company_name": None, "items": [{"item_code_sku": "A", "ordered_qty": None}]}
    batch = _batch([_doc("order.pdf", data)])
    rows = flatten_batch(batch)
    assert rows[0]["company_name"] is None
    assert rows[0]["ordered_qty"] is None


def test_flatten_batch_drops_non_dict_items():
    data = {
        "order_no": "SO-1001",
        "company_name": "ABC",
        "items": ["junk", {"item_code_sku": "A", "ordered_qty": 1}],
    }
    batch = _batch([_doc("order.pdf", data)])
    rows = flatten_batch(batch)
    assert len(rows) == 1


def test_flatten_batch_invoice_no_items():
    data = {"invoice_number": "INV-1", "total": 100.0}
    batch = _batch([_doc("inv.pdf", data)], usecase="invoice")
    rows = flatten_batch(batch)
    assert len(rows) == 1
    assert rows[0]["invoice_number"] == "INV-1"
    assert rows[0]["total"] == 100.0


# --- batch_columns ---------------------------------------------------------


def test_batch_columns_deterministic_order():
    batch = _batch([], usecase="sales_order_ub")
    cols = batch_columns(batch)
    assert cols[0] == "source_file"
    assert "order_no" in cols
    assert "company_name" in cols
    assert "item_code_sku" in cols
    assert "ordered_qty" in cols


def test_batch_columns_match_usecase_config():
    batch = _batch([], usecase="sales_order_ub")
    cols = batch_columns(batch)
    # source_file + 12 doc fields + 6 item fields = 19
    assert len(cols) == 1 + 12 + 6


# --- batch_to_frame ---------------------------------------------------------


def test_batch_to_frame_returns_dataframe():
    data = {
        "order_no": "SO-1001",
        "company_name": "ABC",
        "items": [{"item_code_sku": "A", "ordered_qty": 1}],
    }
    batch = _batch([_doc("order.pdf", data)])
    frame = batch_to_frame(batch)
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 1
    assert list(frame.columns) == batch_columns(batch)


def test_batch_to_frame_empty():
    batch = _batch([])
    frame = batch_to_frame(batch)
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 0
    assert list(frame.columns) == batch_columns(batch)


def test_batch_to_frame_preserves_numeric_types():
    data = {
        "order_no": "SO-1001",
        "company_name": "ABC",
        "items": [{"item_code_sku": "A", "ordered_qty": 10}],
    }
    batch = _batch([_doc("order.pdf", data)])
    frame = batch_to_frame(batch)
    assert pd.api.types.is_integer_dtype(frame["ordered_qty"])
