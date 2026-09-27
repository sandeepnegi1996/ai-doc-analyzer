"""Which backend to talk to, and which model to ask it for. All env-driven.

Two settings, deliberately not one. `LLM_PROVIDER` picks the server; the model
id only means something *to that server*, so it is configured per provider:

    GROQ_MODEL=openai/gpt-oss-120b     # Groq ids are namespaced vendor/model
    OLLAMA_MODEL=qwen3:8b               # Ollama ids are local tags name:tag
    OPENAI_MODEL=gpt-4o-mini            # OpenAI ids are bare names

A single global `LLM_MODEL` is still honoured as an override, but it ranks
*below* the per-provider variable, so a leftover `LLM_MODEL=openai/gpt-oss-120b`
can no longer be sent to a local Ollama server that would answer "model not
found" for it.

`LLM_PROVIDER` is read once at import time -- switching backends needs a
restart. `resolve_model()` reads the environment on every call, so the model
can change without one.
"""

import os

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434/v1",
)

# A default model per provider, because a model name is only meaningful for the
# backend that serves it -- "openai/gpt-oss-120b" is a Groq id, and passing it
# to Ollama yields a confusing "model not found" from the wrong server.
PROVIDER_DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",   # free on Groq, supports strict JSON schema mode
    "ollama": "llama3.1",
    "openai": "gpt-4o-mini",
}

# The env var that overrides the default for each provider.
PROVIDER_MODEL_ENV = {
    "groq": "GROQ_MODEL",
    "ollama": "OLLAMA_MODEL",
    "openai": "OPENAI_MODEL",
}

# Single-override escape hatch, used only when the provider's own var is unset.
GLOBAL_MODEL_ENV = "LLM_MODEL"

# Providers whose model ids carry no vendor namespace: an Ollama tag is
# "name:tag" ("qwen3:8b") and an OpenAI id is bare ("gpt-4o-mini"). A "/" in
# the configured model therefore means a namespaced id -- a Groq one -- is on
# its way to a server that cannot resolve it. Groq is deliberately absent from
# this set: it only ever serves namespaced ids today, but a vendor dropping that
# convention should not hard-fail startup, so the reverse mistake is left to the
# provider's own error message.
UNSLUGGED_PROVIDERS = frozenset({"ollama", "openai"})


class ModelConfigError(ValueError):
    """The configured model id cannot be served by the configured provider."""


def resolve_model(provider=None):
    """The model id to use, for `provider` (default: the configured provider).

    Precedence: `<PROVIDER>_MODEL` -> `LLM_MODEL` -> the built-in default.
    Read from the environment on every call, so a changed value takes effect
    without a restart.

    Raises:
        ModelConfigError: no model is configured or known for this provider, or
            the resolved id is another provider's naming convention.
    """
    name = (provider or LLM_PROVIDER or "").strip().lower()
    specific_var = PROVIDER_MODEL_ENV.get(name)
    specific = (os.getenv(specific_var) or "").strip() if specific_var else ""
    override = (os.getenv(GLOBAL_MODEL_ENV) or "").strip()

    if specific:
        model, source = specific, specific_var
    elif override:
        model, source = override, GLOBAL_MODEL_ENV
    else:
        model, source = PROVIDER_DEFAULT_MODELS.get(name), "the built-in default"

    suggested_var = specific_var or GLOBAL_MODEL_ENV
    if not model:
        raise ModelConfigError(
            f"No model configured for LLM_PROVIDER={name!r} and none is known. "
            f"Set {suggested_var}."
        )
    if name in UNSLUGGED_PROVIDERS and "/" in model:
        shape = (
            "e.g. 'llama3.1' or 'qwen3:8b'"
            if name == "ollama"
            else "e.g. 'gpt-4o-mini'"
        )
        raise ModelConfigError(
            f"Model {model!r} (from {source}) is a namespaced id that {name} "
            f"cannot resolve; {name} model ids are plain names, {shape}. "
            f"Set {suggested_var} to a {name} model, or set LLM_PROVIDER to the "
            f"provider {model!r} belongs to."
        )
    return model
