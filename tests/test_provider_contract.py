"""The provider contract: the same test, run against every backend.

`provider_factory` is parameterised over Groq, Ollama and OpenAI, so each test
below executes three times. If a backend ever starts behaving differently --
silently ignoring a schema, wrapping it wrongly, swallowing an empty response --
one of these fails instead of the difference surfacing in production.

The point is the shared contract, not exhaustive per-backend coverage: the wire
envelope genuinely differs between these APIs, and that is asserted explicitly
in the tables at the bottom.
"""

from dataclasses import FrozenInstanceError

import pytest
from conftest import RAW_RESPONSE, FakeClient

from providers import GroqProvider, MissingApiKeyError, OllamaProvider, OpenAIProvider
from providers.base import EmptyResponseError, ProviderCapabilities

PROMPT = "Extract the fields from: INV-123, total 1699.48"
SCHEMA = {
    "type": "object",
    "properties": {"invoice_number": {"type": ["string", "null"]}},
    "required": ["invoice_number"],
    "additionalProperties": False,
}


# --- the contract, identical for every provider ----------------------------


def test_returns_the_raw_response_untouched(provider_factory):
    provider = provider_factory(content=RAW_RESPONSE)
    assert provider.extract(prompt=PROMPT, json_schema=SCHEMA) == RAW_RESPONSE


def test_sends_the_prompt_it_was_given(provider_factory):
    """No prompt construction inside a provider: what it gets is what the model sees."""
    provider = provider_factory()
    provider.extract(prompt=PROMPT, json_schema=SCHEMA)
    assert provider.client.last_request["messages"] == [
        {"role": "user", "content": PROMPT}
    ]


def test_wraps_the_bare_schema_itself(provider_factory):
    """The schema arrives unwrapped; the envelope is the provider's own job."""
    provider = provider_factory()
    provider.extract(prompt=PROMPT, json_schema=SCHEMA)
    envelope = provider.client.last_request["response_format"]
    assert envelope["type"] == "json_schema"
    assert envelope["json_schema"]["schema"] == SCHEMA
    assert envelope["json_schema"]["name"] == provider.schema_name


def test_sends_the_configured_model_and_generation_arguments(provider_factory):
    provider = provider_factory(model="some/model")
    provider.extract(prompt=PROMPT, json_schema=SCHEMA, max_tokens=512, temperature=0.3)
    request = provider.client.last_request
    assert request["model"] == "some/model"
    assert request["max_tokens"] == 512
    assert request["temperature"] == 0.3


def test_falls_back_to_json_mode_when_no_schema_is_given(provider_factory):
    provider = provider_factory()
    provider.extract(prompt=PROMPT)
    assert provider.client.last_request["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("content", ["", None])
def test_empty_response_raises(provider_factory, content):
    provider = provider_factory(content=content)
    with pytest.raises(EmptyResponseError):
        provider.extract(prompt=PROMPT, json_schema=SCHEMA)


def test_capabilities_are_declared_and_frozen(provider_factory):
    capabilities = provider_factory().capabilities
    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.structured_output is True
    assert isinstance(capabilities.json_schema, bool)
    with pytest.raises(FrozenInstanceError):
        capabilities.vision = True  # frozen: a backend's abilities are not mutable


# --- what legitimately differs between backends ---------------------------

# provider class -> the extra key its json_schema envelope accepts. Ollama's
# compatibility endpoint rejects `strict`; Groq and OpenAI honour it.
STRICT_SUPPORTS = {
    GroqProvider: True,
    OllamaProvider: False,
    OpenAIProvider: True,
}

# provider class -> what this app may rely on it doing.
CAPABILITIES = {
    GroqProvider: ProviderCapabilities(
        structured_output=True, json_schema=True, vision=False
    ),
    OllamaProvider: ProviderCapabilities(
        structured_output=True, json_schema=True, vision=False
    ),
    OpenAIProvider: ProviderCapabilities(
        structured_output=True, json_schema=True, vision=False
    ),
}

# provider class -> the env var it refuses to start without.
API_KEY_VARS = {GroqProvider: "GROQ_API_KEY", OpenAIProvider: "OPENAI_API_KEY"}

CLASS_IDS = [cls.__name__ for cls in STRICT_SUPPORTS]


def _build(provider_class, monkeypatch, content=RAW_RESPONSE):
    key_var = API_KEY_VARS.get(provider_class)
    if key_var:
        monkeypatch.setenv(key_var, "test-key")
    provider = provider_class(model="test-model")
    provider.client = FakeClient(content)
    return provider


@pytest.mark.parametrize("provider_class", list(STRICT_SUPPORTS), ids=CLASS_IDS)
def test_each_backend_sends_the_envelope_its_api_accepts(provider_class, monkeypatch):
    envelope = _build(provider_class, monkeypatch).build_response_format(SCHEMA)["json_schema"]
    assert ("strict" in envelope) is STRICT_SUPPORTS[provider_class]
    assert envelope["schema"] == SCHEMA


@pytest.mark.parametrize("provider_class", list(CAPABILITIES), ids=CLASS_IDS)
def test_each_backend_declares_its_capabilities(provider_class, monkeypatch):
    assert _build(provider_class, monkeypatch).capabilities == CAPABILITIES[provider_class]


@pytest.mark.parametrize(
    "provider_class, key_var",
    list(API_KEY_VARS.items()),
    ids=list(API_KEY_VARS),
)
def test_api_backed_providers_require_their_key(provider_class, key_var, monkeypatch):
    monkeypatch.delenv(key_var, raising=False)
    with pytest.raises(MissingApiKeyError):
        provider_class(model="test-model")


def test_ollama_needs_no_key():
    assert OllamaProvider(model="llama3.1").capabilities.json_schema is True
