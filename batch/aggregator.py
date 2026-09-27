"""Batch aggregator: flatten a BatchResult into table rows.

One row = one line item from one document. Document-level fields repeat down
the rows. The schema is deterministic -- it comes from the usecase config,
not from whichever document happened to finish first.

Missing values stay as None/NaN until the presentation layer.
"""

from typing import Any

from batch.processor import BatchResult
from extractor import load_usecase
from result_tables import field_label


def flatten_batch(batch: BatchResult) -> list[dict[str, Any]]:
    """Flatten a BatchResult into a list of row dicts.

    Each row has: source_file, all document fields, all item fields.
    Documents with no items produce a single row with null item fields.
    Failed documents produce no rows.
    """
    config = load_usecase(batch.usecase)
    doc_fields = config.get("fields", [])
    item_fields = config.get("items", [])

    rows: list[dict[str, Any]] = []
    for doc in batch.succeeded:
        if doc.data is None:
            continue
        source_file = doc.filename
        doc_row = {f["key"]: doc.data.get(f["key"]) for f in doc_fields}
        items = [item for item in (doc.data.get("items") or []) if isinstance(item, dict)]

        if items:
            for item in items:
                row: dict[str, Any] = {"source_file": source_file}
                row.update(doc_row)
                row.update({f["key"]: item.get(f["key"]) for f in item_fields})
                rows.append(row)
        else:
            row = {"source_file": source_file}
            row.update(doc_row)
            for f in item_fields:
                row[f["key"]] = None
            rows.append(row)

    return rows


def batch_columns(batch: BatchResult) -> list[str]:
    """The deterministic column order for a batch result.

    source_file + document fields + item fields, matching the usecase config.
    """
    config = load_usecase(batch.usecase)
    doc_keys = [f["key"] for f in config.get("fields", [])]
    item_keys = [f["key"] for f in config.get("items", [])]
    return ["source_file"] + doc_keys + item_keys


def batch_to_frame(batch: BatchResult):
    """Convenience: flatten and build a DataFrame with the deterministic schema."""
    import pandas as pd

    rows = flatten_batch(batch)
    columns = batch_columns(batch)
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)
