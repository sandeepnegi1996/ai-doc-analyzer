import streamlit as st
import requests

st.title("Document Analyzer MVP")
uploaded = st.file_uploader("Upload PDF or Image",  type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"])

if uploaded and st.button("Analyze"):
    files = {"file": (uploaded.name, uploaded.getvalue())}
    res = requests.post("http://localhost:8000/analyze", files=files)
    st.json(res.json())