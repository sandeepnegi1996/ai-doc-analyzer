For your current project, I would **not** implement this as “upload 2 files and call `analyze_document()` twice.” That works technically, but it creates problems when you later move from 2 → 5 → 50 documents.

The better design is to introduce a small **batch-processing layer** while keeping your existing single-document extractor unchanged.

## 1. Define the exact behavior first

For `sales_order_ub`, user should be able to:

```text
Select: Sales Order UB

Upload:
┌─────────────────────────────┐
│ order_001.pdf               │
│ order_002.pdf               │
└─────────────────────────────┘

             ↓

          Analyze

             ↓

┌──────────────┬────────────┬──────────────┬─────────────┐
│ Order No     │ Company    │ Item Code    │ Quantity    │
├──────────────┼────────────┼──────────────┼─────────────┤
│ SO-1001      │ ABC Ltd    │ SKU-101      │ 10          │
│ SO-1001      │ ABC Ltd    │ SKU-102      │ 20          │
│ SO-1002      │ XYZ Ltd    │ SKU-201      │ 5           │
│ SO-1002      │ XYZ Ltd    │ SKU-202      │ 15          │
└──────────────┴────────────┴──────────────┴─────────────┘
```

The important design decision is:

> **Each PDF remains an independent extraction unit, but the UI combines their results into one batch result.**

Don't send both PDFs together to the LLM initially.

---

# 2. Recommended architecture

I would introduce:

```text
                    Streamlit
                       │
                       ▼
                Batch Processor
                       │
              ┌────────┴────────┐
              ▼                 ▼
         PDF #1             PDF #2
              │                 │
              ▼                 ▼
      analyze_document()  analyze_document()
              │                 │
              ▼                 ▼
        Result #1            Result #2
              │                 │
              └────────┬────────┘
                       ▼
                 Batch Aggregator
                       │
                       ▼
                Final Table
```

This is deliberately simple.

Your existing:

```python
analyze_document(...)
```

remains responsible for **one document**.

A new component becomes responsible for:

```python
analyze_documents(...)
```

or:

```python
process_batch(...)
```

---

# 3. Don't modify the extractor to understand batches

This is an important separation.

I would keep:

```python
analyze_document(
    file_bytes,
    filename,
    usecase
)
```

as the atomic operation.

Then introduce:

```python
process_batch(
    documents,
    usecase
)
```

Conceptually:

```python
def process_batch(documents, usecase):

    results = []

    for document in documents:
        result = analyze_document(
            document.bytes,
            document.name,
            usecase
        )

        results.append(result)

    return results
```

Later, this can become concurrent processing without changing `analyze_document()`.

---

# 4. Why this separation matters

Today:

```text
2 PDFs
```

Later:

```text
5 PDFs
```

Eventually:

```text
100 PDFs
```

Your architecture becomes:

```text
process_batch()
       │
       ├── process document 1
       ├── process document 2
       ├── process document 3
       ├── ...
       └── process document N
```

Later you can change the implementation from:

```python
for document in documents:
    analyze_document(...)
```

to a bounded concurrent worker model:

```text
             Batch
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
     Worker  Worker  Worker
       │       │       │
      PDF     PDF     PDF
```

without changing your UI or extraction logic.

---

# 5. Important: preserve document identity

This is one of the most important design decisions.

Don't return only:

```json
{
  "order_no": "SO-1001",
  "company_name": "ABC"
}
```

because once you have multiple documents, you need to know where the result came from.

Every result should contain:

```json
{
  "document_id": "...",
  "filename": "order_001.pdf",
  "status": "completed",
  "data": {
    "order_no": "SO-1001",
    "company_name": "ABC"
  }
}
```

For now, `document_id` can simply be generated in memory.

Later it can become a persistent UUID.

---

# 6. Batch result should have a clear contract

I'd define something like:

```python
BatchResult
```

conceptually:

```json
{
  "batch_id": "batch-123",
  "usecase": "sales_order_ub",
  "documents": [
    {
      "document_id": "doc-1",
      "filename": "order_001.pdf",
      "status": "completed",
      "data": {}
    },
    {
      "document_id": "doc-2",
      "filename": "order_002.pdf",
      "status": "completed",
      "data": {}
    }
  ]
}
```

Then the table is generated from `documents`.

This is better than making the table itself your internal data structure.

---

# 7. The biggest question: what should one table row represent?

This needs to be explicitly defined.

For your `sales_order_ub`, you already have:

```text
header fields
+
items[]
```

So I recommend:

> **One row = one line item from one sales order.**

For example:

| Source PDF    | Order No | Company | Item Code | Item Name | Ordered Qty | Pack Size |
| ------------- | -------- | ------- | --------- | --------- | ----------: | --------- |
| order_001.pdf | SO-1001  | ABC Ltd | SKU-101   | Product A |          10 | 5         |
| order_001.pdf | SO-1001  | ABC Ltd | SKU-102   | Product B |          20 | 10        |
| order_002.pdf | SO-1002  | XYZ Ltd | SKU-201   | Product C |           5 | 5         |

This is much more useful than producing:

```text
PDF 1 → giant nested JSON
PDF 2 → giant nested JSON
```

and trying to display that directly.

---

# 8. Flattening strategy

Your existing extraction result probably resembles:

```json
{
  "order_no": "SO-1001",
  "company_name": "ABC Ltd",
  "contact_person": "John",
  "items": [
    {
      "item_code_sku": "SKU-101",
      "item_name": "Product A",
      "ordered_qty": 10,
      "pack_size": 5
    },
    {
      "item_code_sku": "SKU-102",
      "item_name": "Product B",
      "ordered_qty": 20,
      "pack_size": 10
    }
  ]
}
```

The batch aggregator transforms it into:

```text
Document
   │
   ├── Header
   │
   └── Items
          │
          ├── Item 1 → row
          └── Item 2 → row
```

Result:

```python
[
    {
        "source_file": "order_001.pdf",
        "order_no": "SO-1001",
        "company_name": "ABC Ltd",
        "item_code_sku": "SKU-101",
        "item_name": "Product A",
        "ordered_qty": 10,
        "pack_size": 5,
    },
    {
        "source_file": "order_001.pdf",
        "order_no": "SO-1001",
        "company_name": "ABC Ltd",
        "item_code_sku": "SKU-102",
        "item_name": "Product B",
        "ordered_qty": 20,
        "pack_size": 10,
    }
]
```

This becomes extremely easy for Streamlit to render.

---

# 9. Don't flatten inside `extractor.py`

I'd create a separate component:

```text
extractor.py
       │
       ▼
Batch processor
       │
       ▼
Result aggregator
```

For example:

```text
batch/
├── processor.py
└── aggregator.py
```

Responsibilities:

### `processor.py`

```text
PDF → extraction result
```

### `aggregator.py`

```text
multiple extraction results
        ↓
flat tabular representation
```

This separation will become very useful later.

---

# 10. Partial failure is extremely important

Suppose:

```text
PDF 1 → successful
PDF 2 → OCR failure
```

**Do not fail the entire batch.**

Your result should be:

```text
Batch completed with errors

✓ order_001.pdf
  Successfully extracted

✗ order_002.pdf
  Failed
  Reason: OCR_FAILED
```

And the table should still show PDF 1.

This is one of the most important production design decisions.

---

# 11. Batch status

I'd define:

```text
PENDING
PROCESSING
COMPLETED
PARTIAL_SUCCESS
FAILED
```

For example:

```text
2 PDFs

PDF 1 → COMPLETED
PDF 2 → COMPLETED

Batch → COMPLETED
```

Or:

```text
PDF 1 → COMPLETED
PDF 2 → FAILED

Batch → PARTIAL_SUCCESS
```

If both fail:

```text
Batch → FAILED
```

---

# 12. UI design

I would keep the UI very simple initially.

### Current

```text
Document Type
[ Sales Order UB ]

Upload Document
[ Choose File ]

[ Analyze ]
```

### New

```text
Document Type
[ Sales Order UB ]

Upload Documents
[ Choose up to 2 PDFs ]

Selected files:

✓ order_001.pdf
✓ order_002.pdf

[ Analyze Documents ]
```

Then:

```text
Processing Documents

order_001.pdf     █████████████  Completed
order_002.pdf     █████████████  Completed
```

Then:

```text
Extraction Summary

Documents: 2
Successful: 2
Failed: 0
Items extracted: 14
```

Then:

```text
Final Results

┌────────────┬──────────┬───────────┬──────────┐
│ Order No   │ Company  │ SKU       │ Quantity │
├────────────┼──────────┼───────────┼──────────┤
│ SO-1001    │ ABC      │ SKU-001   │ 10       │
│ SO-1001    │ ABC      │ SKU-002   │ 20       │
│ SO-1002    │ XYZ      │ SKU-003   │ 15       │
└────────────┴──────────┴───────────┴──────────┘
```

---

# 13. I would NOT use parallel processing yet

For exactly two PDFs, initially:

```python
for file in files:
    analyze_document(...)
```

is completely acceptable.

Don't prematurely introduce:

```text
ThreadPoolExecutor
asyncio
Celery
Redis
Kafka
```

The first goal is to establish the **batch contract**.

Once 2-document processing works correctly, concurrency can be introduced as an implementation detail.

---

# 14. But design for concurrency

Even though we process sequentially initially, make this interface:

```python
process_batch(files, usecase)
```

rather than putting the loop directly in Streamlit.

Later:

```python
def process_batch(files, usecase):
    ...
```

can internally become:

```text
process_batch
     │
     ├── max_workers = 2
     │
     ├── PDF 1
     └── PDF 2
```

Then eventually:

```text
max_workers = configurable
```

For 5 files, you might process:

```text
2 or 3 concurrently
```

rather than 5 simultaneously, depending on LLM/OCR/resource limits.

---

# 15. Don't send the two PDFs to one LLM request

I would explicitly avoid:

```text
PDF 1 ─┐
       ├──> LLM
PDF 2 ─┘
```

Initially.

Instead:

```text
PDF 1 → LLM → Result 1

PDF 2 → LLM → Result 2
```

Reasons:

* Easier error isolation
* Easier retry
* Better traceability
* Easier debugging
* Easier scaling
* Document-specific confidence
* Provider token limits don't become a batch problem
* One bad document doesn't contaminate another

---

# 16. Deduplication

Since you're going toward a production system, I'd also calculate a hash per uploaded document:

```text
SHA-256(PDF bytes)
```

For now this can simply be metadata:

```json
{
  "document_id": "...",
  "filename": "order_001.pdf",
  "sha256": "..."
}
```

Later this allows:

```text
same document uploaded twice
        ↓
detect duplicate
```

This becomes particularly valuable when you move to larger batches.

---

# 17. Ordering

Preserve upload order.

If user uploads:

```text
1. order_A.pdf
2. order_B.pdf
```

the final output should remain deterministic:

```text
order_A rows
order_B rows
```

Even if later processing becomes concurrent.

Don't rely on completion order.

This is a subtle but important design decision.

---

# 18. Table schema should be deterministic

Don't dynamically create columns based on whichever PDF happens to finish first.

Get the schema from:

```text
sales_order_ub.json
```

For example:

```text
Header fields
+
item fields
+
source_file
```

Then all rows conform to the same columns.

That prevents:

```text
PDF 1 has field A
PDF 2 has field B
```

from producing a messy DataFrame.

---

# 19. Missing values

Suppose:

```text
PDF 1 → pack_size = 10
PDF 2 → pack_size = null
```

The table should show:

```text
Pack Size
10
—
```

Do not silently remove the column or convert missing data to arbitrary strings.

Keep:

```python
None / NaN
```

until the presentation layer.

---

# 20. Keep raw results alongside the table

This is another important decision.

Internally:

```text
BatchResult
│
├── document_results
│     ├── raw extraction
│     ├── validation
│     └── metadata
│
└── table_rows
```

The table is only a **view**.

Don't make the DataFrame the source of truth.

This allows you later to support:

```text
Table
JSON
Excel
CSV
API response
```

all from the same underlying batch result.

---

# 21. This naturally enables Excel export

Once you have:

```python
table_rows
```

adding:

```text
Download CSV
Download Excel
```

becomes trivial.

For example:

```text
[ Download CSV ] [ Download Excel ]
```

That would be a very useful next step for sales-order processing.

---

# 22. Suggested internal model

I'd introduce something like:

```python
@dataclass
class DocumentResult:
    document_id: str
    filename: str
    status: str
    data: dict | None
    error_code: str | None = None
    error_message: str | None = None
```

Then:

```python
@dataclass
class BatchResult:
    batch_id: str
    usecase: str
    documents: list[DocumentResult]
```

And separately:

```python
def flatten_sales_orders(
    batch_result: BatchResult
) -> list[dict]:
    ...
```

This is clean and extensible.

---

# 23. One additional decision: batch-level validation

Don't just validate each PDF.

You can eventually validate the **combined result**.

For example, detect:

```text
same order_no appears in PDF 1 and PDF 2
```

or:

```text
duplicate SKU/order combination
```

or:

```text
same order number but conflicting company
```

This becomes:

```text
Document validation
        ↓
Batch validation
        ↓
Final table
```

But I would **not implement complex cross-document validation in v1**. Keep the hook available.

---

# 24. Recommended implementation phases

For your specific project, I would implement this in four small steps.

### Phase 1 — Batch domain model

Add:

```text
DocumentResult
BatchResult
process_batch()
```

No UI changes yet.

---

### Phase 2 — Streamlit upload

Change the sales-order UI to:

```text
accept_multiple_files=True
```

but enforce:

```text
maximum = 2
```

Also validate:

```text
PDF only
at least 1
maximum 2
```

---

### Phase 3 — Aggregation

Implement:

```text
DocumentResult[]
       ↓
sales_order_ub aggregator
       ↓
flat rows
       ↓
pandas DataFrame
       ↓
st.dataframe()
```

The key transformation is:

```text
2 PDFs
   ↓
2 extraction results
   ↓
N line items
   ↓
N table rows
```

---

### Phase 4 — Reliability

Add:

```text
partial failures
document IDs
status
error codes
processing duration
structured logging
```

Then the system is ready to scale the maximum from:

```text
2 → 5
```

without changing the fundamental architecture.

---

# 25. Final architecture I recommend for your current codebase

```text
                         app.py
                           │
                           ▼
                  ┌─────────────────┐
                  │ Batch Processor │
                  └────────┬────────┘
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
             Document 1          Document 2
                 │                   │
                 ▼                   ▼
        analyze_document()   analyze_document()
                 │                   │
                 ▼                   ▼
          DocumentResult       DocumentResult
                 │                   │
                 └─────────┬─────────┘
                           ▼
                  ┌─────────────────┐
                  │ BatchResult     │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Sales Order     │
                  │ Aggregator      │
                  └────────┬────────┘
                           │
                           ▼
                     table_rows
                           │
                           ▼
                     DataFrame
                           │
                           ▼
                     Streamlit UI
```

### Most important design principle

**Don't make “2 PDFs” a special case.**

Implement:

```python
process_batch(documents)
```

where the current implementation happens to support:

```text
1–2 documents
```

Then increasing to:

```text
1–5
```

should primarily be a configuration change:

```python
MAX_BATCH_SIZE = 5
```

rather than an architectural rewrite.

I don't currently have your actual `app.py`, `extractor.py`, and `sales_order_ub.json` files attached in this conversation, so I would **not invent exact patches against them**. If you provide those files, the next step should be to map this design onto your existing code and produce the exact implementation changes, including the Streamlit UI and aggregation logic.




