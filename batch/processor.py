"""Batch processor: run analyze_document() over multiple files.

Each document is processed independently. The processor never sends multiple
documents to one LLM call -- error isolation, traceability, and per-document
confidence all depend on this.

Partial failure is a first-class outcome: one bad document does not fail the
batch. The batch status is derived from per-document statuses.
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import extractor
from extractor import ExtractorError

logger = logging.getLogger(__name__)

class DocumentStatus:
    """Per-document status constants."""

    COMPLETED = "completed"
    FAILED = "failed"


class BatchStatus:
    """Batch-level status constants."""

    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


# Error codes for failed documents
ERROR_EXTRACTION = "extraction_failed"
ERROR_OCR = "ocr_failed"
ERROR_UNSUPPORTED = "unsupported_file"
ERROR_OVERSIZED = "oversized_document"
ERROR_MISSING_KEY = "missing_api_key"
ERROR_UNKNOWN_USECASE = "unknown_usecase"
ERROR_LLM = "llm_failure"


@dataclass
class DocumentResult:
    """The outcome of processing one document in a batch.

    `data` holds the extracted fields on success, None on failure.
    `error_code` / `error_message` describe the failure when status != completed.
    `sha256` is the content hash for future deduplication.
    `duration_seconds` is the wall-clock time for this document's extraction.
    """

    document_id: str
    filename: str
    status: str
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    sha256: str = ""
    duration_seconds: float = 0.0


@dataclass
class BatchResult:
    """The outcome of processing a batch of documents.

    `status` is derived: completed if all succeeded, failed if all failed,
    partial_success otherwise. `documents` preserves upload order.
    """

    batch_id: str
    usecase: str
    documents: list[DocumentResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.documents:
            return BatchStatus.FAILED
        succeeded = sum(1 for d in self.documents if d.status == DocumentStatus.COMPLETED)
        if succeeded == len(self.documents):
            return BatchStatus.COMPLETED
        if succeeded == 0:
            return BatchStatus.FAILED
        return BatchStatus.PARTIAL_SUCCESS

    @property
    def succeeded(self) -> list[DocumentResult]:
        return [d for d in self.documents if d.status == DocumentStatus.COMPLETED]

    @property
    def failed(self) -> list[DocumentResult]:
        return [d for d in self.documents if d.status != DocumentStatus.COMPLETED]

    @property
    def total_items(self) -> int:
        """Total line items across all successful documents."""
        return sum(len(d.data.get("items") or []) for d in self.succeeded if d.data)


def _error_code_from_exception(error: ExtractorError) -> str:
    """Map an ExtractorError subclass to a stable error code string."""
    from extractor import (
        EmptyDocumentError,
        LLMFailureError,
        MissingApiKeyError,
        OversizedDocumentError,
        UnknownUsecaseError,
        UnsupportedFileError,
    )

    if isinstance(error, MissingApiKeyError):
        return ERROR_MISSING_KEY
    if isinstance(error, UnknownUsecaseError):
        return ERROR_UNKNOWN_USECASE
    if isinstance(error, UnsupportedFileError):
        return ERROR_UNSUPPORTED
    if isinstance(error, OversizedDocumentError):
        return ERROR_OVERSIZED
    if isinstance(error, EmptyDocumentError):
        return ERROR_OCR
    if isinstance(error, LLMFailureError):
        return ERROR_LLM
    return ERROR_EXTRACTION


def process_batch(
    documents: list[tuple[bytes, str]],
    usecase: str,
) -> BatchResult:
    """Process a batch of documents, each through analyze_document().

    `documents` is a list of (file_bytes, filename) tuples in upload order.
    Each document is processed independently; a failure in one does not
    prevent the others from being processed.

    Returns a BatchResult with per-document status, timing, and error info.
    """
    batch_id = f"batch-{uuid.uuid4().hex[:8]}"
    logger.info("Starting batch %s for usecase %s with %d document(s)", batch_id, usecase, len(documents))

    results: list[DocumentResult] = []
    for file_bytes, filename in documents:
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        start = time.monotonic()

        logger.info("Processing %s (id=%s, sha256=%s...)", filename, doc_id, sha256[:12])

        try:
            data = extractor.analyze_document(file_bytes, filename, usecase)
            duration = time.monotonic() - start
            results.append(DocumentResult(
                document_id=doc_id,
                filename=filename,
                status=DocumentStatus.COMPLETED,
                data=data,
                sha256=sha256,
                duration_seconds=round(duration, 3),
            ))
            logger.info("Completed %s in %.2fs", filename, duration)
        except ExtractorError as error:
            duration = time.monotonic() - start
            code = _error_code_from_exception(error)
            results.append(DocumentResult(
                document_id=doc_id,
                filename=filename,
                status="failed",
                data=None,
                error_code=code,
                error_message=str(error),
                sha256=sha256,
                duration_seconds=round(duration, 3),
            ))
            logger.warning("Failed %s: %s - %s", filename, code, str(error))
        except Exception as error:
            duration = time.monotonic() - start
            results.append(DocumentResult(
                document_id=doc_id,
                filename=filename,
                status="failed",
                data=None,
                error_code=ERROR_EXTRACTION,
                error_message=str(error),
                sha256=sha256,
                duration_seconds=round(duration, 3),
            ))
            logger.exception("Unexpected error processing %s", filename)

    batch = BatchResult(batch_id=batch_id, usecase=usecase, documents=results)
    logger.info(
        "Batch %s finished: status=%s, succeeded=%d, failed=%d",
        batch_id, batch.status, len(batch.succeeded), len(batch.failed),
    )
    return batch
