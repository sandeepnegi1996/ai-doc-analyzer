"""`get_llm_provider()`: name -> class, and the two ways configuration fails."""

import pytest

import providers
from config import PROVIDER_DEFAULT_MODELS
from providers import (
    GroqProvider,
    ModelMismatchError,
    OllamaProvider,
    OpenAIProvider,
    UnsupportedProviderError,
)

# LLM_PROVIDER is bound into this module at import time, so the tests set it
# here rather than in the environment; monkeypatch undoes it afterwards.
FACTORIES = [
    ("groq", GroqProvider, {"GROQ_API_KEY": "test-key"}),
    ("ollama", OllamaProvider, {}),
    ("openai", OpenAIProvider, {"OPENAI_API_KEY": "test-key"}),
]

IDS = [name for name, _, _ in FACTORIES]


@pytest.mark.parametrize("name, expected, env", FACTORIES, ids=IDS)
def test_builds_the_named_provider_with_its_resolved_model(name, expected, env, monkeypatch):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(providers, "LLM_PROVIDER", name)
    provider = providers.get_llm_provider()
    assert isinstance(provider, expected)
    assert provider.model == PROVIDER_DEFAULT_MODELS[name]


def test_per_provider_model_env_var_reaches_the_client(monkeypatch):
    monkeypatch.setattr(providers, "LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    assert providers.get_llm_provider().model == "qwen3:8b"


def test_unknown_provider_name(monkeypatch):
    monkeypatch.setattr(providers, "LLM_PROVIDER", "mystery")
    with pytest.raises(UnsupportedProviderError) as error:
        providers.get_llm_provider()
    assert "groq, ollama, openai" in str(error.value)


def test_another_providers_model_id_is_refused_up_front(monkeypatch):
    """The failure this guards: a Groq model id aimed at a local Ollama server."""
    monkeypatch.setattr(providers, "LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    with pytest.raises(ModelMismatchError) as error:
        providers.get_llm_provider()
    assert "OLLAMA_MODEL" in str(error.value)
