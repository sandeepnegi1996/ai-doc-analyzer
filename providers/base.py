"""The provider abstraction: what every LLM backend must do, and nothing more.

A provider connects, sends, receives and returns. It never reads a document,
never builds a prompt and never validates a result -- `extractor.py` does all of
that and hands over a finished prompt plus a bare JSON Schema. That boundary is
what makes a backend swappable without touching document logic.

`ProviderCapabilities` exists because "send a JSON schema" is not a universal
ability: Groq honours one in strict mode, Ollama's support depends on the model,
and some backends only offer plain JSON mode. Declaring it lets the caller ask
for what the backend can actually do instead of discovering it from a 400.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    """Base class for every failure a provider can raise.

    The provider layer raises only these types, so the extraction layer can
    classify a failure by type instead of matching on error message strings.
    """


class MissingApiKeyError(ProviderError):
    """The provider's API key is not configured in the environment."""


class UnsupportedProviderError(ProviderError):
    """The configured provider name does not match any known provider."""


class ModelMismatchError(ProviderError):
    """The configured model id cannot be served by the configured provider."""


class EmptyResponseError(ProviderError):
    """The model returned no content."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What this app may rely on a backend doing, declared rather than assumed.

    These describe the *configured* backend as this app talks to it, not every
    feature its vendor ships -- `vision` is False everywhere today because
    documents always reach the LLM as text after OCR/PDF parsing.

        structured_output: the API can be asked for JSON at all.
        json_schema:       it can be handed a schema, not just "reply with
                          JSON"; models that cannot honour one return free-form
                          JSON instead, which parsing then has to survive.
        vision:            it accepts image content (unused so far).
    """

    structured_output: bool
    json_schema: bool
    vision: bool


class LLMProvider(ABC):
    """Provider-agnostic extraction interface.

    Implementations own every provider-specific concern: SDK client, model
    name, credentials, how a JSON schema is expressed on the wire, and what the
    backend can do. The only contract is `extract`, which receives a bare JSON
    Schema object and returns the model's raw response string.
    """

    schema_name = "extracted_fields"
    strict = True
    display_name = "LLM"

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """What this provider supports. Read before asking for anything exotic."""
        raise NotImplementedError

    @abstractmethod
    def extract(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 2000,
        temperature: float = 0,
    ) -> str:
        """Extract structured data from a document prompt.

        `prompt` is already fully built; `json_schema` is a bare JSON Schema
        object, not a provider-specific envelope, and is None when the caller
        knows this backend cannot honour one. Returns the raw LLM response
        string; parsing and validation are handled by the extraction layer, not
        the provider.
        """
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    """`extract` for backends that speak the OpenAI chat-completions API.

    Groq, Ollama's compatibility endpoint and OpenAI all do, so the request body
    and the response read are written once here instead of three times.
    Subclasses supply credentials, the client (`self.client`), the model
    (`self.model`), their capabilities, and whether the API accepts a
    strict-schema flag.
    """

    # False for endpoints whose json_schema object rejects the extra `strict` key.
    supports_strict_schema = True

    def build_response_format(self, json_schema):
        """Wrap the bare schema in this API's `response_format` envelope.

        No schema means plain JSON mode: the prompt already demands a single
        JSON object, so the model is constrained to one without a schema.
        """
        if not json_schema:
            return {"type": "json_object"}
        envelope = {"name": self.schema_name, "schema": json_schema}
        if self.supports_strict_schema:
            envelope["strict"] = self.strict
        return {"type": "json_schema", "json_schema": envelope}

    def extract(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 2000,
        temperature: float = 0,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=self.build_response_format(json_schema),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = self.read_content(response)
        if not content:
            raise EmptyResponseError(f"{self.display_name} returned an empty response")
        return content

    def read_content(self, response) -> str:
        """Pull the assistant text out of a chat-completion response."""
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        return getattr(message, "content", None) or ""
