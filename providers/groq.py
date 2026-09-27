"""Groq backend: OpenAI-compatible chat completions with strict JSON schema."""

import os

from groq import Groq

from .base import (
    MissingApiKeyError,
    OpenAICompatibleProvider,
    ProviderCapabilities,
)


class GroqProvider(OpenAICompatibleProvider):
    display_name = "Groq"

    def __init__(self, model: str):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise MissingApiKeyError("GROQ_API_KEY is not configured")

        self.client = Groq(api_key=api_key)
        self.model = model

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Vision is False: this app always hands the model text, and the
        # strict-schema mode below already pins the response shape.
        return ProviderCapabilities(
            structured_output=True,
            json_schema=True,
            vision=False,
        )
