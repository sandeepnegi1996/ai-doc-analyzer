"""Tests for the UI result formatting in result_tables.py.

Offline and deterministic: no Streamlit runtime, no LLM, no fixtures. These
guard the three regressions the original UI had -- a document field repeated
into a narrow column (15-line-tall rows), stringified values (numeric sorting
broken), and unreadable column labels -- plus the export payloads the download
buttons serve.
"""

import json

import pandas as pd
import pytest

from result_tables import (
    INDEX_LABEL,
    column_config_for,
    combined_table,
    drop_empty_columns,
    export_basename,
    field_label,
    file_stem,
    to_csv_bytes,
    to_json_bytes,
)

ORDER_CONFIG = {
    "name": "Sales Order UB",
    "fields": [
        {"key": "order_no", "type": "string"},
        {"key": "delivery_address", "type": "string"},
        {"key": "department", "type": "string"},  # typically not found
        {"key": "expected_delivery_date", "type": "date"},
    ],
    "items": [
        {"key": "item_code_sku", "type": "string"},
        {"key": "ordered_qty", "type": "int"},
    ],
}

# A real delivery address, not a toy one: the wrapping regression only shows up
# with a genuinely long value, which is what the original UI got wrong.
LONG_ADDRESS = (
    "Hyderabad Eye Institute, KAR Main Stores, L.V.Prasad Marg, H.No. 8-2-269/19/A, "
    "Kallam Anji Reddy Campus, Road No.-2, Banjara Hills, Hyderabad - 500034"
)

RESULT_WITH_ITEMS = {
    "order_no": "UBPL/26-27/SO685",
    "delivery_address": LONG_ADDRESS,
    "department": None,
    "expected_delivery_date": "2026-09-17",
    "items": [
        {"item_code_sku": "GTX36599", "ordered_qty": 1},
        {"item_code_sku": "GTX77314", "ordered_qty": 1},
    ],
    "raw_text_preview": "SALES ORDER",
}

RESULT_NO_ITEMS = {
    "order_no": "INV-1",
    "delivery_address": "a",
    "department": "",
    "expected_delivery_date": "2026-01-01",
}


class TestFieldLabel:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("item_code_sku", "Item Code SKU"),
            ("delivery_address", "Delivery Address"),
            ("order_no", "Order No"),
            ("contact_no_2", "Contact No 2"),
        ],
    )
    def test_labels_are_readable(self, key, expected):
        assert field_label(key) == expected

    def test_sku_is_not_mangled(self):
        # The old `key.replace("_", " ").title()` produced "Item Code Sku".
        assert "Sku" not in field_label("item_code_sku")


class TestCombinedTable:
    def test_one_row_per_line_item(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert len(frame) == 2

    def test_no_items_gives_a_single_row(self):
        frame, _ = combined_table(RESULT_NO_ITEMS, ORDER_CONFIG)
        assert len(frame) == 1
        assert frame["Order No"].iloc[0] == "INV-1"

    def test_document_and_item_fields_share_one_table(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert list(frame.columns) == [
            INDEX_LABEL,
            "Order No",
            "Delivery Address",
            "Department",
            "Expected Delivery Date",
            "Item Code SKU",
            "Ordered Qty",
        ]

    def test_document_fields_repeat_down_the_rows(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert frame["Order No"].tolist() == ["UBPL/26-27/SO685"] * 2

    def test_rows_are_numbered_from_one(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert frame[INDEX_LABEL].tolist() == [1, 2]

    def test_fields_metadata_matches_column_order(self):
        _, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert [f["key"] for f in fields] == [
            "order_no",
            "delivery_address",
            "department",
            "expected_delivery_date",
            "item_code_sku",
            "ordered_qty",
        ]

    def test_raw_text_is_not_a_column(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert "Raw Text Preview" not in frame.columns

    def test_non_dict_items_are_skipped(self):
        result = dict(RESULT_WITH_ITEMS, items=["junk", {"ordered_qty": 2}])
        frame, _ = combined_table(result, ORDER_CONFIG)
        assert len(frame) == 1

    def test_int_field_stays_numeric(self):
        """A stringified quantity sorts as 1, 10, 2 -- the old UI bug.

        Uses 1, 2 and 10 on purpose: as text those sort 1, 10, 2, so the
        assertion only passes if the column is genuinely numeric.
        """
        result = dict(
            RESULT_WITH_ITEMS,
            items=[
                {"item_code_sku": "A", "ordered_qty": 1},
                {"item_code_sku": "B", "ordered_qty": 2},
                {"item_code_sku": "C", "ordered_qty": 10},
            ],
        )
        frame, _ = combined_table(result, ORDER_CONFIG)
        assert frame["Ordered Qty"].tolist() == [1, 2, 10]
        assert pd.api.types.is_integer_dtype(frame["Ordered Qty"])
        assert sorted(frame["Ordered Qty"]) == [1, 2, 10]

    def test_nulls_stay_null_not_the_string_none(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        assert pd.isna(frame["Department"].iloc[0])
        assert "None" not in to_csv_bytes(frame).decode("utf-8")


class TestDropEmptyColumns:
    def test_all_null_column_is_hidden(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        visible, hidden = drop_empty_columns(frame)
        assert "Department" in hidden
        assert "Department" not in visible.columns

    def test_blank_string_column_counts_as_empty(self):
        frame, _ = combined_table(RESULT_NO_ITEMS, ORDER_CONFIG)
        visible, hidden = drop_empty_columns(frame)
        assert "Department" in hidden

    def test_populated_columns_are_kept(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        visible, hidden = drop_empty_columns(frame)
        assert hidden == ["Department"]
        assert "Order No" in visible.columns
        assert "Ordered Qty" in visible.columns

    def test_index_column_is_never_dropped(self):
        frame, _ = combined_table(RESULT_NO_ITEMS, ORDER_CONFIG)
        visible, _ = drop_empty_columns(frame)
        assert INDEX_LABEL in visible.columns

    def test_a_partially_filled_column_is_kept(self):
        result = dict(
            RESULT_WITH_ITEMS, items=[{"item_code_sku": "A", "ordered_qty": 1}, {}]
        )
        frame, _ = combined_table(result, ORDER_CONFIG)
        visible, hidden = drop_empty_columns(frame)
        assert "Item Code SKU" in visible.columns
        assert "Item Code SKU" not in hidden

    def test_row_count_is_unchanged(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        visible, _ = drop_empty_columns(frame)
        assert len(visible) == len(frame)


def _config_for_value(value):
    """column_config for a single-item table whose SKU column holds `value`."""
    result = {
        "order_no": "SO-1",
        "delivery_address": "a",
        "department": "d",
        "expected_delivery_date": "2026-01-01",
        "items": [{"item_code_sku": value, "ordered_qty": 1}],
    }
    frame, fields = combined_table(result, ORDER_CONFIG)
    return column_config_for(frame, fields)


class TestColumnConfig:
    def test_int_field_gets_an_integer_number_column(self):
        # st.column_config.*Column() returns a plain dict, not an object.
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        config = column_config_for(frame, fields)
        # st.column_config.*Column() returns a plain dict, not an object.
        assert config["Ordered Qty"]["type_config"]["type"] == "number"
        assert config["Ordered Qty"]["type_config"]["format"] == "%d"

    def test_date_stays_a_text_column(self):
        # `date` is a plain string here; a DateColumn would error on it.
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        config = column_config_for(frame, fields)
        assert config["Expected Delivery Date"]["type_config"]["type"] == "text"

    def test_long_free_text_gets_a_wide_column(self):
        """The old narrow address column wrapped to ~15 lines in one row."""
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        config = column_config_for(frame, fields)
        assert config["Delivery Address"]["width"] == "large"

    def test_short_code_gets_a_narrow_column(self):
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        config = column_config_for(frame, fields)
        assert config["Item Code SKU"]["width"] == "small"

    def test_every_visible_column_is_configured(self):
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        visible, _ = drop_empty_columns(frame)
        config = column_config_for(visible, fields)
        assert set(config) == set(visible.columns)

    def test_index_column_is_a_small_integer_column(self):
        frame, fields = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        config = column_config_for(frame, fields)
        assert config[INDEX_LABEL]["type_config"]["type"] == "number"
        assert config[INDEX_LABEL]["width"] == "small"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("GTX36599", "small"),          # 8 chars, fits the ~75px column
            ("Hyderabad Eye Institute", "medium"),  # 24 chars, fits ~200px
            (LONG_ADDRESS, "large"),        # 195 chars, needs the full width
        ],
    )
    def test_width_buckets_follow_content_length(self, value, expected):
        config = _config_for_value(value)
        assert config["Item Code SKU"]["width"] == expected


class TestExports:
    def test_csv_has_a_header_and_one_row_per_item(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        lines = to_csv_bytes(frame).decode("utf-8").strip().splitlines()
        assert lines[0].startswith("#,Order No")
        assert len(lines) == 3

    def test_csv_keeps_empty_fields_the_view_hides(self):
        frame, _ = combined_table(RESULT_WITH_ITEMS, ORDER_CONFIG)
        visible, _ = drop_empty_columns(frame)
        assert "Department" not in visible.columns
        assert "Department" in to_csv_bytes(frame).decode("utf-8")

    def test_csv_without_items_is_a_one_row_summary(self):
        frame, _ = combined_table(RESULT_NO_ITEMS, ORDER_CONFIG)
        assert len(to_csv_bytes(frame).decode("utf-8").strip().splitlines()) == 2

    def test_json_export_keeps_nulls_and_raw_text(self):
        payload = json.loads(to_json_bytes(RESULT_WITH_ITEMS).decode("utf-8"))
        assert payload["raw_text_preview"] == "SALES ORDER"
        assert payload["department"] is None

    def test_json_export_handles_unserialisable_values(self):
        blob = to_json_bytes({"when": object()}).decode("utf-8")
        assert json.loads(blob)["when"]  # default=str fallback, not a crash


class TestFilenames:
    @pytest.mark.parametrize(
        ("uploaded", "expected"),
        [
            ("order.pdf", "order"),
            ("purchase order (final).pdf", "purchase_order_final"),
            ("../../etc/passwd.pdf", "etc_passwd"),
            ("", "document"),
            (None, "document"),
        ],
    )
    def test_stem_is_sanitised(self, uploaded, expected):
        assert file_stem(uploaded) == expected

    def test_basename_combines_usecase_and_source(self):
        assert export_basename("sales_order_ub", "SO 77.pdf") == "sales_order_ub_SO_77"
