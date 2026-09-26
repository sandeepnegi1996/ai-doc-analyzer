import streamlit as st
import requests

st.title("Document Analyzer MVP")
try:
    usecases_response = requests.get("http://localhost:8000/usecases", timeout=5)
    usecases_response.raise_for_status()
    usecases = usecases_response.json()
except requests.RequestException as error:
    st.error(f"Could not load document types: {error}")
    st.stop()

usecase_names = {item["key"]: item["name"] for item in usecases}
usecase = st.selectbox(
    "Document type",
    options=list(usecase_names),
    format_func=lambda key: usecase_names[key],
)
uploaded = st.file_uploader("Upload PDF or Image", type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"])

if uploaded and st.button("Analyze"):
    files = {"file": (uploaded.name, uploaded.getvalue())}
    data = {"usecase": usecase}
    try:
        response = requests.post(
            "http://localhost:8000/analyze", files=files, data=data, timeout=60
        )
        response.raise_for_status()
        st.json(response.json())
    except requests.RequestException as error:
        st.error(f"Analysis failed: {error}")