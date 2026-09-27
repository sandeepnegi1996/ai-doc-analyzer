"""Batch processing layer: multi-document extraction and aggregation.

Keeps the single-document extractor unchanged. Each document is an independent
extraction unit; the batch layer combines results into one batch result.
"""

from batch.aggregator import flatten_batch
from batch.processor import (
    BatchResult,
    BatchStatus,
    DocumentResult,
    DocumentStatus,
    process_batch,
)

__all__ = [
    "BatchResult",
    "BatchStatus",
    "DocumentResult",
    "DocumentStatus",
    "flatten_batch",
    "process_batch",
]
