import os

import streamlit as st

from extractor import (
    ExtractorError,
    LLMFailureError,
    MissingApiKeyError,
    OversizedDocumentError,
    analyze_document,
    list_usecases,
)


def load_api_key():
    """Resolve the Groq key: env var first, then Streamlit secrets.

    extractor.get_client() reads os.environ only, so a Cloud secret has to be
    copied into the env before extraction. Precedence:
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


# --- Custom CSS for darker table headers ---
st.markdown("""
<style>
    thead th {
        background-color: #1a1f2e !important;
        color: #e0e0e0 !important;
        border-bottom: 2px solid #00d4aa !important;
    }
</style>
""", unsafe_allow_html=True)


# --- Login Screen ---
st.title("Document Analyzer MVP")

if "logged_in" not in st.session_state or not st.session_state.logged_in:
    login_username = st.text_input("Username", key="login_user")
    login_password = st.text_input("Password", type="password", key="login_pass")

    if st.button("Login"):
        if login_username == "sandy" and login_password == "sandy":
            st.session_state.logged_in = True
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
uploaded = st.file_uploader("Upload PDF or Image", type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"])

if not load_api_key():
    st.warning("GROQ_API_KEY is not configured. Set it in `.env` locally, or add it "
                "under Tools > Secrets as `GROQ_API_KEY` when deployed.")

if uploaded and st.button("Analyze"):
    try:
        with st.spinner("Analyzing..."):
            result = analyze_document(uploaded.getvalue(), uploaded.name, usecase)
        items = result.pop("items", None)
        if items:
            order_fields = {k.replace("_", " ").title(): v for k, v in result.items() if k != "raw_text_preview"}
            rows = []
            for item in items:
                row = {k: "" if v is None else str(v) for k, v in order_fields.items()}
                row.update({k.replace("_", " ").title(): "" if v is None else str(v) for k, v in item.items()})
                rows.append(row)
            st.table(rows)
        else:
            st.table({
                key.replace("_", " ").title(): ["" if value is None else str(value)]
                for key, value in result.items()
                if key != "raw_text_preview"
            })
        with st.expander("Extracted document text"):
            st.text(result.get("raw_text_preview", ""))
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
