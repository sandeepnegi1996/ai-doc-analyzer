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

from extractor import (
    RAW_TEXT_KEY,
    ExtractorError,
    LLMFailureError,
    MissingApiKeyError,
    OversizedDocumentError,
    analyze_document,
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


def render_results(result, config, source_name, usecase_key):
    """Render one analysis as a single table, plus downloads and raw text."""
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
uploaded = st.file_uploader(
    "Upload PDF or Image", type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"]
)

if not load_api_key():
    st.warning("GROQ_API_KEY is not configured. Set it in `.env` locally, or add it "
               "under Tools > Secrets as `GROQ_API_KEY` when deployed.")

if uploaded and st.button("Analyze", type="primary"):
    try:
        with st.spinner("Analyzing..."):
            result = analyze_document(uploaded.getvalue(), uploaded.name, usecase)
        st.session_state.analysis = {
            "result": result,
            "usecase": usecase,
            "filename": uploaded.name,
        }
    except OversizedDocumentError:
        st.error("Oversized document")
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
        analysis["result"],
        load_usecase(analysis["usecase"]),
        analysis["filename"],
        analysis["usecase"],
    )
