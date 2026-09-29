"""Wiring tests for app.render_results.

Exercises the real render function with Streamlit's display calls stubbed, so
the assertions are about what the user is actually shown and offered -- which
table, with which column config, and with which download filenames. Runs
offline in bare mode; no Streamlit server and no LLM involved.

Importing app.py executes the page in bare mode, where st.stop() is a no-op, so
this does not need a running app.
"""

import pandas as pd
import app
import pytest
import streamlit as st

LONG_ADDRESS = (
    "Hyderabad Eye Institute, KAR Main Stores, L.V.Prasad Marg, H.No. 8-2-269/19/A, "
    "Kallam Anji Reddy Campus, Road No.-2, Banjara Hills, Hyderabad - 500034"
)

ORDER_CONFIG = {
    "name": "Sales Order UB",
    "fields": [
        {"key": "order_no", "type": "string"},
        {"key": "delivery_address", "type": "string"},
        {"key": "department", "type": "string"},
    ],
    "items": [{"key": "ordered_qty", "type": "int"}],
}

RESULT_WITH_ITEMS = {
    "order_no": "UBPL/26-27/SO685",
    "delivery_address": LONG_ADDRESS,
    "department": None,
    "items": [{"ordered_qty": 1}, {"ordered_qty": 2}],
    "raw_text_preview": "SALES ORDER",
}


class _NoOp:
    """Minimal stand-in for a Streamlit context manager (a column slot)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class Recorder:
    """Captures what render_results hands to each Streamlit display call."""

    def __init__(self, monkeypatch):
        self.dataframes = []
        self.downloads = []
        self.texts = []
        self.headers = []
        self.captions = []
        self.infos = []
        self.checkboxes = {}

        monkeypatch.setattr(
            st, "dataframe", lambda data, **kw: self.dataframes.append((data, kw))
        )
        monkeypatch.setattr(
            st,
            "download_button",
            lambda label, **kw: self.downloads.append((label, kw)),
        )
        monkeypatch.setattr(st, "text", lambda value, **kw: self.texts.append(value))
        monkeypatch.setattr(
            st, "markdown", lambda value, **kw: self.headers.append(value)
        )
        monkeypatch.setattr(st, "caption", lambda value, **kw: self.captions.append(value))
        monkeypatch.setattr(st, "info", lambda value, **kw: self.infos.append(value))
        # render_results does `left, right, spacer = st.columns([1, 1, 4])`, so
        # the stub must yield exactly as many enterable slots as it is asked for.
        monkeypatch.setattr(
            st, "columns", lambda spec, **kw: [_NoOp() for _ in spec]
        )

        def checkbox(label, value=False, **kwargs):
            # A real checkbox keeps its state across reruns; honour whatever
            # the test has staged for this label, else the widget's default.
            self.checkboxes[label] = self.checkboxes.get(label, value)
            return self.checkboxes[label]

        monkeypatch.setattr(st, "checkbox", checkbox)

    def set_checkbox(self, value):
        self.checkboxes["Show fields that were not found"] = value

    @property
    def shown(self):
        return self.dataframes[0][0]

    @property
    def labels(self):
        return [label for label, _ in self.downloads]


@pytest.fixture
def recorder(monkeypatch):
    return Recorder(monkeypatch)


def test_one_table_only(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    assert len(recorder.dataframes) == 1, "details and items share a single table"


def test_single_table_contains_both_field_groups(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    columns = list(recorder.shown.columns)
    assert "Order No" in columns and "Delivery Address" in columns
    assert "Ordered Qty" in columns


def test_one_row_per_line_item(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    assert len(recorder.shown) == 2


def test_empty_fields_are_always_shown(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    assert "Department" in recorder.shown.columns


def test_caption_counts_all_fields(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    caption = recorder.captions[0]
    assert "4 fields" in caption
    assert "2 line items" in caption


def test_caption_counts_columns_not_rows(recorder):
    """`len(df)` is the row count, so the field count must use `.columns`."""
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    expected_fields = len(recorder.shown.columns) - 1  # minus the '#' index
    assert f"{expected_fields} fields" in recorder.captions[0]
    assert f"{len(recorder.shown)} rows" in recorder.captions[0]


def test_toggle_brings_empty_fields_back(recorder):
    recorder.set_checkbox(True)
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    assert "Department" in recorder.shown.columns
    assert "empty field" not in recorder.captions[0]


def test_no_items_renders_a_single_row_table(recorder):
    app.render_results(
        {"order_no": "SO-1", "delivery_address": "a", "department": "d"},
        ORDER_CONFIG,
        "order.pdf",
        "sales_order_ub",
    )
    assert len(recorder.dataframes) == 1
    assert len(recorder.shown) == 1


def test_no_fields_at_all_shows_a_message_not_an_empty_table(recorder):
    app.render_results({}, {"fields": [], "items": []}, "order.pdf", "invoice")
    assert recorder.dataframes == []
    assert any("No fields" in message for message in recorder.infos)


def test_download_filenames_are_derived_from_usecase_and_upload(recorder):
    app.render_results(
        RESULT_WITH_ITEMS, ORDER_CONFIG, "purchase order (final).pdf", "sales_order_ub"
    )
    filenames = [kw["file_name"] for _, kw in recorder.downloads]
    assert filenames == [
        "sales_order_ub_purchase_order_final.csv",
        "sales_order_ub_purchase_order_final.json",
    ]


def test_csv_download_keeps_fields_the_view_hides(recorder):
    """The view hides empty columns; the export must not lose them."""
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    csv_kw = next(kw for label, kw in recorder.downloads if label == "Download CSV")
    assert "Department" in csv_kw["data"].decode("utf-8")


def test_download_payloads_are_bytes_with_matching_mime(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    for _, kw in recorder.downloads:
        assert isinstance(kw["data"], bytes)
    csv_kw, json_kw = (kw for _, kw in recorder.downloads)
    assert csv_kw["mime"] == "text/csv"
    assert json_kw["mime"] == "application/json"


def test_table_is_full_width_without_the_pandas_index(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    _, kwargs = recorder.dataframes[0]
    assert kwargs["hide_index"] is True
    assert kwargs["width"] == "stretch"


def test_every_shown_column_has_a_column_config(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    _, kwargs = recorder.dataframes[0]
    assert set(kwargs["column_config"]) == set(recorder.shown.columns)


def test_raw_text_is_shown_in_the_expander(recorder):
    app.render_results(RESULT_WITH_ITEMS, ORDER_CONFIG, "order.pdf", "sales_order_ub")
    assert recorder.texts == ["SALES ORDER"]
