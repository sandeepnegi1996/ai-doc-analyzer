"""FastAPI shell over extractor.py. All logic lives in extractor.py.

This module is transport only: it adapts HTTP in/out and maps ExtractorError
subclasses onto status codes. Streamlit does not use this server.
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException

from extractor import (
    MODEL,
    ExtractorError,
    LLMFailureError,
    MissingApiKeyError,
    OversizedDocumentError,
    UnknownUsecaseError,
    analyze_document,
    list_usecases,
)

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL,
                "object": "model",
                "created": 0,
                "owned_by": "groq",
            }
        ],
    }


@app.get("/usecases")
async def get_usecases():
    return list_usecases()


@app.post("/analyze")
async def analyze(usecase: str = Form(...), file: UploadFile = File(...)):
    try:
        return analyze_document(await file.read(), file.filename or "", usecase)
    except UnknownUsecaseError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except MissingApiKeyError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except OversizedDocumentError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except LLMFailureError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ExtractorError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
