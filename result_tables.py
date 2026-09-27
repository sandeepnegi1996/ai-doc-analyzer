"""Result formatting for the UI: one combined table, labels, and export payloads.

Split out of app.py so it is importable and testable without executing the
Streamlit script (importing app.py runs the whole page, including the login
gate's st.stop()).

Presentation only -- no extraction logic and no LLM knowledge lives here. The
shapes come from a usecase config, so a new usecase in usecases/ needs no
change to this module.

The results are ONE table: document fields and line-item fields side by side,
one row per line item. Document fields repeat down the rows, which is the same
flattened shape an ERP export uses -- it is what makes the table copy-pasteable
into a spreadsheet in a single action. Two separate tables meant two copy
operations and two things to keep visually in sync.
"""

import json
import os
import re

import pandas as pd
import streamlit as st

# Title-casing a snake_case key mangles these ("Item Code Sku"), so they are
# restored by hand. Only affects column headers, never values.
ACRONYMS = {
    "id": "ID",
    "no": "No",
    "po": "PO",
    "qty": "Qty",
    "sku": "SKU",
    "ub": "UB",
}

# Leading row-number column, so a row can be referred to unambiguously once the
# table is sorted or filtered ("row 4").
INDEX_LABEL = "#"

# Column width buckets, chosen by the longest value in the column. A long
# free-text field in a narrow column is what stretched a single row to ~15 lines
# tall in the original UI, so width is driven by content, not by a flat default.
#
# Streamlit's presets are roughly small=75px, medium=200px, large=400px, and a
# character averages ~7px in the default font. The cutoffs are therefore the
# longest string that still fits on one line at that width -- measured, not
# guessed, so `medium` really does mean "no wrapping".
WIDTH_SMALL_MAX = 10
WIDTH_MEDIUM_MAX = 28


def field_label(key):
    """'item_code_sku' -> 'Item Code SKU'."""
    return " ".join(
        ACRONYMS.get(word.lower(), word.capitalize()) for word in str(key).split("_")
    )


def frame_from_rows(rows, columns):
    """Build a DataFrame with native dtypes.

    Deliberately not stringified: the original UI cast every value with
    `str()`, so quantities sorted as 1, 10, 2 and numbers were left-aligned.
    """
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def combined_table(result, config, source_file=None):
    """The single results table: one row per line item, all fields as columns.

    Returns (frame, fields) where `fields` are the usecase field dicts in the
    same order as the columns after the index, for building column_config.

    With no line items the table is a single row of document fields, so both
    cases render through exactly the same code path.

    When `source_file` is provided (batch mode) a "Source File" column is
    inserted right after the index, so each row traces back to its document.
    """
    doc_fields = config.get("fields", [])
    item_fields = config.get("items", [])
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]

    doc_row = {field_label(f["key"]): result.get(f["key"]) for f in doc_fields}
    item_labels = [field_label(f["key"]) for f in item_fields]

    if items:
        rows = []
        for item in items:
            row = dict(doc_row)
            row.update({label: item.get(f["key"]) for label, f in zip(item_labels, item_fields)})
            rows.append(row)
    else:
        rows = [dict(doc_row)] if doc_fields else []

    if source_file is not None:
        for row in rows:
            row["source_file"] = source_file
        source_label = "Source File"
        columns = [INDEX_LABEL, source_label] + list(doc_row) + item_labels
    else:
        columns = [INDEX_LABEL] + list(doc_row) + item_labels

    rows = [{INDEX_LABEL: n, **row} for n, row in enumerate(rows, start=1)]
    return frame_from_rows(rows, columns), doc_fields + item_fields


def drop_empty_columns(frame):
    """Remove columns whose every value is null, returning (frame, hidden labels).

    An all-empty column is the main reason a wide single table becomes
    unreadable: it still claims horizontal space, squeezing the columns that
    have data. In a typical sales order that is a third of the columns
    (department, second contact, email) and every one of them is blank.

    Callers disclose the count rather than dropping silently, and the JSON
    export always keeps every field.
    """
    keep, hidden = [], []
    for label in frame.columns:
        column = frame[label]
        if column.empty or column.isna().all() or (column == "").all():
            hidden.append(label)
        else:
            keep.append(label)
    return frame[keep], hidden


def _width_for(values):
    """small/medium/large from the longest rendered value in a column."""
    longest = max((len(str(value)) for value in values if not pd.isna(value)), default=0)
    if longest <= WIDTH_SMALL_MAX:
        return "small"
    if longest <= WIDTH_MEDIUM_MAX:
        return "medium"
    return "large"


def column_config_for(frame, fields):
    """Type and size every column of the combined table.

    Types come from the usecase config so numbers sort and align as numbers; a
    `date` is a plain string in this app, so it stays a TextColumn -- a
    DateColumn needs real date objects and would render these as errors.
    """
    config = {}
    for label in frame.columns:
        if label == INDEX_LABEL:
            config[label] = st.column_config.NumberColumn(
                INDEX_LABEL, format="%d", width="small"
            )
            continue
        field = next(
            (f for f in fields if field_label(f["key"]) == label),
            {"type": "string"},
        )
        kind = field.get("type", "string")
        if kind in ("int", "float"):
            config[label] = st.column_config.NumberColumn(
                label, format="%d" if kind == "int" else "%.2f"
            )
        else:
            config[label] = st.column_config.TextColumn(
                label, width=_width_for(frame[label].tolist())
            )
    return config


def file_stem(name):
    """A filename-safe stem from the uploaded name.

    Runs of unsafe characters collapse to a single underscore, so
    "purchase order (final).pdf" becomes "purchase_order_final" rather than
    "purchase_order__final".
    """
    stem = os.path.splitext(name or "")[0]
    safe = "".join(c if c.isalnum() or c in "-_" else " " for c in stem)
    safe = re.sub(r"[\s_]+", "_", safe).strip("_")
    return safe or "document"


def export_basename(usecase_key, source_name):
    """e.g. 'sales_order_ub_purchase_order_final' -- safe for a Content-Disposition."""
    return f"{usecase_key}_{file_stem(source_name)}"


def to_csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8")


def to_json_bytes(payload):
    return json.dumps(payload, indent=2, default=str).encode("utf-8")
