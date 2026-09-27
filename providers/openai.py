"""OpenAI backend: OpenAI-compatible chat completions with strict JSON schema."""

import os

from openai import OpenAI

from .base import (
    MissingApiKeyError,
    OpenAICompatibleProvider,
    ProviderCapabilities,
)


class OpenAIProvider(OpenAICompatibleProvider):
    display_name = "OpenAI"

    def __init__(self, model: str):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise MissingApiKeyError("OPENAI_API_KEY is not configured")

        self.client = OpenAI(api_key=api_key)
        self.model = model

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Vision is False: the chat-completions path this provider uses is
        # text-only, and images never reach it.
        return ProviderCapabilities(
            structured_output=True,
            json_schema=True,
            vision=False,
        )
