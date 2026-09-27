"""Ollama backend, via the local server's OpenAI-compatible endpoint.

No API key, and no `strict` flag: the compatibility endpoint accepts a
`json_schema` object without that extra key, and whether the schema is honoured
at all depends on the model that is pulled locally -- hence
`ProviderCapabilities` being read before the schema is sent.
"""

from openai import OpenAI

from .base import (
    OpenAICompatibleProvider,
    ProviderCapabilities,
)


class OllamaProvider(OpenAICompatibleProvider):
    display_name = "Ollama"
    supports_strict_schema = False

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434/v1",
    ):
        self.model = model
        # The compatibility endpoint ignores the key but the SDK insists on one.
        self.client = OpenAI(base_url=base_url, api_key="ollama")

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            structured_output=True,
            json_schema=True,
            vision=False,
        )
