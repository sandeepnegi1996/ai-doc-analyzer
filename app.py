"""Streamlit UI for the document analyzer.

Transport/UI only: extraction lives in extractor.py and the LLM backends in
providers/. This module's job is to turn a result dict into something a human
can read -- tables that sort, search and download.

Results are cached in `st.session_state.analysis` rather than rendered straight
out of the button handler, so the tables survive any later widget interaction
(touching the document-type dropdown no longer makes them vanish).
"""

import os

import streamlit as st

from batch import process_batch
from extractor import (
    RAW_TEXT_KEY,
    ExtractorError,
    LLMFailureError,
    MissingApiKeyError,
    OversizedDocumentError,
    list_usecases,
    load_usecase,
)
from result_tables import (
    column_config_for,
    combined_table,
    drop_empty_columns,
    export_basename,
    to_csv_bytes,
    to_json_bytes,
)

st.set_page_config(
    page_title="Document Analyzer",
    page_icon="📄",
    layout="wide",
)


def render_results(result, config, source_name, usecase_key, batch_result=None):
    """Render one analysis as a single table, plus downloads and raw text.

    When `batch_result` is provided the results are rendered from the batch:
    per-document status, a combined table across all documents, and batch-level
    exports. Otherwise the original single-document path is used.
    """
    if batch_result is not None:
        _render_batch_results(batch_result, config, usecase_key)
        return

    stem = export_basename(usecase_key, source_name)
    frame, fields = combined_table(result, config)
    item_count = len(result.get("items") or [])

    st.subheader(config.get("name", "Extracted fields"))

    if frame.empty or len(frame.columns) <= 1:
        st.info("No fields were extracted from this document.")
    else:
        # All-empty columns are hidden by default: they still claim horizontal
        # space, which squeezes the columns that do have data. The count is
        # disclosed below and the toggle brings them back, so nothing is lost
        # silently -- and the JSON export always carries every field.
        show_empty = st.checkbox(
            "Show fields that were not found",
            value=False,
            key="show_empty_fields",
        )
        if show_empty:
            visible, hidden = frame, []
        else:
            visible, hidden = drop_empty_columns(frame)

        # len(df) is the row count, not the column count -- count columns.
        summary = f"{len(visible.columns) - 1} fields · {len(frame)} rows"
        if item_count:
            summary += f" · {item_count} line item{'s' if item_count != 1 else ''}"
        if hidden:
            summary += f" · {len(hidden)} empty field{'s' if len(hidden) != 1 else ''} hidden"
        st.caption(summary)

        st.dataframe(
            visible,
            hide_index=True,
            width="stretch",
            column_config=column_config_for(visible, fields),
        )

    # st.dataframe's own toolbar already offers a download of what is on screen.
    # These are for the complete result: the CSV keeps every field even when the
    # view hides the empty ones, and the JSON keeps nulls and the raw text,
    # which a flattened CSV cannot represent.
    left, right, spacer = st.columns([1, 1, 4])
    with left:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(frame),
            file_name=f"{stem}.csv",
            mime="text/csv",
            width="stretch",
            help="One row per line item, with every extracted field as a column.",
        )
    with right:
        st.download_button(
            "Download JSON",
            data=to_json_bytes(result),
            file_name=f"{stem}.json",
            mime="application/json",
            width="stretch",
            help="The full result, including fields that were not found and the extracted text.",
        )

    raw_text = result.get(RAW_TEXT_KEY, "")
    with st.expander(f"Extracted document text ({len(raw_text):,} characters)"):
        st.text(raw_text)


def _render_batch_results(batch_result, config, usecase_key):
    """Render batch results: per-document status, combined table, exports."""
    from batch.aggregator import flatten_batch
    from result_tables import (
        INDEX_LABEL,
        _width_for,
        column_config_for,
        drop_empty_columns,
        field_label,
        frame_from_rows,
    )

    # --- Per-document status ---
    st.subheader("Document Processing Results")
    for doc in batch_result.documents:
        if doc.status == "completed":
            st.success(f"{doc.filename} - Completed ({doc.duration_seconds:.2f}s)")
        else:
            st.error(f"{doc.filename} - Failed: {doc.error_message}")

    # --- Batch summary ---
    succeeded = len(batch_result.succeeded)
    failed = len(batch_result.failed)
    total = len(batch_result.documents)
    st.info(
        f"Documents: {total} | Successful: {succeeded} | Failed: {failed} "
        f"| Items: {batch_result.total_items}"
    )

    # --- Combined table ---
    if not batch_result.succeeded:
        st.warning("No documents were successfully extracted.")
        return

    rows = flatten_batch(batch_result)
    if not rows:
        st.info("No fields were extracted from the batch.")
        return

    columns = list(rows[0].keys())
    frame = frame_from_rows(rows, columns)

    show_empty = st.checkbox(
        "Show fields that were not found",
        value=False,
        key="show_empty_fields",
    )
    if show_empty:
        visible, hidden = frame, []
    else:
        visible, hidden = drop_empty_columns(frame)

    # Build column config from usecase fields
    doc_fields = config.get("fields", [])
    item_fields = config.get("items", [])
    all_fields = doc_fields + item_fields

    config_dict = {}
    for label in visible.columns:
        if label == INDEX_LABEL:
            config_dict[label] = st.column_config.NumberColumn(
                INDEX_LABEL, format="%d", width="small"
            )
        elif label == "source_file":
            config_dict[label] = st.column_config.TextColumn(label, width="medium")
        else:
            field = next(
                (f for f in all_fields if field_label(f["key"]) == label),
                {"type": "string"},
            )
            kind = field.get("type", "string")
            if kind in ("int", "float"):
                config_dict[label] = st.column_config.NumberColumn(
                    label, format="%d" if kind == "int" else "%.2f"
                )
            else:
                config_dict[label] = st.column_config.TextColumn(
                    label, width=_width_for(visible[label].tolist())
                )

    summary = f"{len(visible.columns) - 1} fields - {len(frame)} rows"
    if hidden:
        summary += f" - {len(hidden)} empty field{'s' if len(hidden) != 1 else ''} hidden"
    st.caption(summary)

    st.dataframe(
        visible,
        hide_index=True,
        width="stretch",
        column_config=config_dict,
    )

    # --- Exports ---
    stem = f"{usecase_key}_batch"
    left, right, spacer = st.columns([1, 1, 4])
    with left:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(frame),
            file_name=f"{stem}.csv",
            mime="text/csv",
            width="stretch",
            help="One row per line item across all documents.",
        )
    with right:
        import json

        st.download_button(
            "Download JSON",
            data=json.dumps(
                {
                    "batch_id": batch_result.batch_id,
                    "usecase": batch_result.usecase,
                    "status": batch_result.status,
                    "documents": [
                        {
                            "filename": d.filename,
                            "status": d.status,
                            "data": d.data,
                            "error_code": d.error_code,
                            "error_message": d.error_message,
                        }
                        for d in batch_result.documents
                    ],
                },
                indent=2,
                default=str,
            ).encode("utf-8"),
            file_name=f"{stem}.json",
            mime="application/json",
            width="stretch",
            help="The full batch result with per-document status and data.",
        )


def load_api_key():
    """Resolve the Groq key: env var first, then Streamlit secrets.

    The providers read os.environ when they are constructed, so a Cloud secret
    has to be copied into the env before extraction. Precedence:
      local dev    -> real env var, or .env (extractor loads it at import)
      Streamlit Cloud -> st.secrets, which never reaches the process env

    st.secrets raises when no secrets.toml exists at all, hence the guard: a
    local run with no key should show the warning below, not a traceback.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets.get("GROQ_API_KEY")
        except Exception:  # no secrets.toml and no env key configured
            api_key = None
    if api_key:
        os.environ["GROQ_API_KEY"] = str(api_key)
    return bool(api_key)


# --- Login Screen ---
st.title("Document Analyzer MVP")

if not st.session_state.get("logged_in"):
    st.text_input("Username", key="login_user")
    st.text_input("Password", type="password", key="login_pass")

    if st.button("Login", type="primary"):
        if st.session_state.login_user == "sandy" and st.session_state.login_pass == "sandy":
            st.session_state.logged_in = True
            st.session_state.pop("analysis", None)
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()

# --- Main App ---
try:
    usecases = list_usecases()
except ExtractorError as error:
    st.error(f"Could not load document types: {error}")
    st.stop()

if not usecases:
    st.error("No document types found in usecases/.")
    st.stop()

usecase_names = {item["key"]: item["name"] for item in usecases}
usecase = st.selectbox(
    "Document type",
    options=list(usecase_names),
    format_func=lambda key: usecase_names[key],
)
uploaded_files = st.file_uploader(
    "Upload Documents",
    type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"],
    accept_multiple_files=True,
)

if not load_api_key():
    st.warning("GROQ_API_KEY is not configured. Set it in `.env` locally, or add it "
               "under Tools > Secrets as `GROQ_API_KEY` when deployed.")

if uploaded_files:
    st.write(f"Selected {len(uploaded_files)} file(s):")
    for f in uploaded_files:
        st.write(f"- {f.name}")

    if len(uploaded_files) > 5:
        st.error("Maximum 5 files allowed. Please remove some files.")
    elif st.button("Analyze Documents", type="primary"):
        try:
            with st.spinner("Analyzing documents..."):
                documents = [(f.getvalue(), f.name) for f in uploaded_files]
                batch_result = process_batch(documents, usecase)
            st.session_state.analysis = {
                "batch_result": batch_result,
                "usecase": usecase,
                "filename": "batch",
            }
        except MissingApiKeyError:
            st.error("Configuration error. Please contact the administrator.")
        except LLMFailureError:
            st.error("Document analysis failed. Please try again.")
        except ExtractorError as error:
            st.error(str(error))
        except Exception:
            st.error("Document analysis failed. Please try again.")

# Rendered from session state, not from the handler above, so the tables persist
# across reruns caused by unrelated widgets.
analysis = st.session_state.get("analysis")
if analysis:
    render_results(
        None,
        load_usecase(analysis["usecase"]),
        analysis["filename"],
        analysis["usecase"],
        batch_result=analysis.get("batch_result"),
    )
