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

st.set_page_config(
    page_title="Document Analyzer",
    page_icon="📄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --- Custom CSS ---
st.markdown("""
<style>
    .main {
        background-color: #0e1117;
        color: #e0e0e0;
    }
    .stApp {
        background-color: #0e1117;
    }
    .stTitle {
        color: #00d4aa;
        text-align: center;
    }
    .stButton > button {
        background-color: #00d4aa;
        color: #0e1117;
        border: none;
        border-radius: 8px;
        font-weight: bold;
        padding: 0.5rem 2rem;
    }
    .stButton > button:hover {
        background-color: #00f5c8;
    }
    .stTextInput > label {
        color: #aaaaaa;
    }
    .stTextInput > div > div > input {
        background-color: #1a1f2e;
        color: #e0e0e0;
        border: 1px solid #333;
        border-radius: 6px;
    }
    .stSelectbox > label {
        color: #aaaaaa;
    }
    .stSelectbox > div > div {
        background-color: #1a1f2e;
        color: #e0e0e0;
        border-radius: 6px;
    }
    .stFileUploader {
        color: #aaaaaa;
    }
    .stAlert {
        background-color: #1a1f2e;
        border-left: 4px solid #00d4aa;
    }
    .stWarning {
        background-color: #2a2010;
        border-left: 4px solid #ffaa00;
    }
    .stError {
        background-color: #2a1010;
        border-left: 4px solid #ff4444;
    }
    .stSidebar {
        background-color: #0e1117;
    }
    .header-text {
        color: #00d4aa;
        font-size: 2.5rem;
        font-weight: 800;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .subheader-text {
        color: #888888;
        font-size: 1rem;
        text-align: center;
        margin-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# --- Login Screen ---
st.markdown('<p class="header-text">📄 Document Analyzer</p>', unsafe_allow_html=True)
st.markdown('<p class="subheader-text">Upload & extract data from your documents</p>', unsafe_allow_html=True)

if "logged_in" not in st.session_state or not st.session_state.logged_in:
    st.markdown("### 🔒 Login Required")
    login_username = st.text_input("Username", key="login_user")
    login_password = st.text_input("Password", type="password", key="login_pass")

    if st.button("Login", use_container_width=True):
        if login_username == "sandy" and login_password == "sandy":
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()

# --- Main App ---
st.markdown("---")

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

if uploaded and st.button("Analyze", use_container_width=True):
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
