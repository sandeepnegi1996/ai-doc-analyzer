"""Model resolution: per-provider ids, and catching one meant for another server."""

import pytest

from config import (
    GLOBAL_MODEL_ENV,
    PROVIDER_DEFAULT_MODELS,
    ModelConfigError,
    resolve_model,
)

MODEL_VARS = ["GROQ_MODEL", "OLLAMA_MODEL", "OPENAI_MODEL", GLOBAL_MODEL_ENV]


@pytest.fixture(autouse=True)
def no_model_configured(monkeypatch):
    """Start from a clean slate: .env or a shell may set any of these."""
    for var in MODEL_VARS:
        monkeypatch.delenv(var, raising=False)


# --- precedence ------------------------------------------------------------


@pytest.mark.parametrize(
    "provider, env_var, model",
    [
        ("groq", "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        ("ollama", "OLLAMA_MODEL", "qwen3:8b"),
        ("openai", "OPENAI_MODEL", "gpt-4o-mini"),
    ],
)
def test_provider_specific_model_wins(provider, env_var, model, monkeypatch):
    monkeypatch.setenv(env_var, model)
    monkeypatch.setenv(GLOBAL_MODEL_ENV, "openai/gpt-oss-120b")
    assert resolve_model(provider) == model


def test_global_model_still_works_as_a_single_override(monkeypatch):
    monkeypatch.setenv(GLOBAL_MODEL_ENV, "openai/gpt-oss-120b")
    assert resolve_model("groq") == "openai/gpt-oss-120b"


@pytest.mark.parametrize("provider, default", list(PROVIDER_DEFAULT_MODELS.items()))
def test_falls_back_to_the_builtin_default(provider, default):
    assert resolve_model(provider) == default


def test_reads_the_environment_on_every_call(monkeypatch):
    """The model can change without a restart; LLM_PROVIDER is the import-time one."""
    monkeypatch.setenv("GROQ_MODEL", "first/model")
    assert resolve_model("groq") == "first/model"
    monkeypatch.setenv("GROQ_MODEL", "second/model")
    assert resolve_model("groq") == "second/model"


def test_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    assert resolve_model(" Ollama ") == "qwen3:8b"


# --- the guard -------------------------------------------------------------


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_rejects_another_providers_namespaced_id(provider, monkeypatch):
    monkeypatch.setenv(GLOBAL_MODEL_ENV, "openai/gpt-oss-120b")
    with pytest.raises(ModelConfigError) as error:
        resolve_model(provider)
    message = str(error.value)
    assert "openai/gpt-oss-120b" in message
    assert f"{provider.upper()}_MODEL" in message  # says which var to fix


def test_groq_accepts_a_namespaced_id(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    assert resolve_model("groq") == "openai/gpt-oss-120b"


def test_unknown_provider_only_resolves_if_a_model_is_configured(monkeypatch):
    with pytest.raises(ModelConfigError):
        resolve_model("mystery")
    # The provider name itself is validated by providers.get_llm_provider(),
    # which refuses an unknown backend before it ever asks for a model.
    monkeypatch.setenv(GLOBAL_MODEL_ENV, "mystery-model")
    assert resolve_model("mystery") == "mystery-model"
