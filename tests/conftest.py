"""Test doubles, so every provider test runs offline and deterministically.

A vendor SDK client is constructed for real (that only reads the key from the
environment) and then replaced with `FakeClient`, which records the request the
provider would have sent. That makes the contract tests assert on the actual
wire payload -- which is the part that silently differs between backends.
"""

from types import SimpleNamespace

import pytest

from providers.base import LLMProvider, ProviderCapabilities

RAW_RESPONSE = '{"invoice_number": "INV-123", "total": "1699.48"}'


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Stands in for a vendor SDK client: the same `chat.completions.create`."""

    def __init__(self, content=RAW_RESPONSE):
        self.chat = SimpleNamespace(completions=FakeCompletions(content))

    @property
    def last_request(self):
        return self.chat.completions.calls[-1]


class StubProvider(LLMProvider):
    """An LLMProvider for extractor tests: records the request, replays a reply."""

    display_name = "Stub"

    def __init__(self, response=RAW_RESPONSE, *, json_schema_supported=True):
        self.response = response
        self.json_schema_supported = json_schema_supported
        self.requests = []

    @property
    def capabilities(self):
        return ProviderCapabilities(
            structured_output=True,
            json_schema=self.json_schema_supported,
            vision=False,
        )

    def extract(self, prompt, json_schema=None, *, max_tokens=2000, temperature=0):
        self.requests.append(
            {
                "prompt": prompt,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.response


# Provider class + the environment it needs to be constructible without a key.
# The contract tests are parameterised over this list, so every new backend is
# held to the same contract simply by being added here.
PROVIDERS = [
    ("GroqProvider", "GROQ_API_KEY"),
    ("OllamaProvider", None),
    ("OpenAIProvider", "OPENAI_API_KEY"),
]


@pytest.fixture(params=PROVIDERS, ids=[name for name, _ in PROVIDERS])
def provider_factory(request, monkeypatch):
    """Build each real provider class with a fake client and its key in the env."""
    from providers import GroqProvider, OllamaProvider, OpenAIProvider

    classes = {
        "GroqProvider": GroqProvider,
        "OllamaProvider": OllamaProvider,
        "OpenAIProvider": OpenAIProvider,
    }
    provider_name, key_var = request.param
    provider_class = classes[provider_name]

    def build(*, content=RAW_RESPONSE, model="test-model", env=True):
        if key_var and env:
            monkeypatch.setenv(key_var, "test-key")
        provider = provider_class(model=model)
        provider.client = FakeClient(content)
        return provider

    return build
