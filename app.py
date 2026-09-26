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
        result = response.json()
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
    except requests.RequestException as error:
        st.error(f"Analysis failed: {error}")