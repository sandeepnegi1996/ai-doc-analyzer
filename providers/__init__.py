"""Provider selection.

`get_llm_provider()` is the only entry point the extraction layer needs. It
reads the configured provider name from config.py and returns the matching
LLMProvider. Adding a backend means adding a module here and one entry in the
two tables below; nothing in extractor.py changes.
"""

from .base import (
    EmptyResponseError,
    LLMProvider,
    MissingApiKeyError,
    ModelMismatchError,
    OpenAICompatibleProvider,
    ProviderCapabilities,
    ProviderError,
    UnsupportedProviderError,
)
from .groq import GroqProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

from config import (
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    ModelConfigError,
    resolve_model,
)

# Backend name -> provider class. One entry per supported provider.
_PROVIDER_CLASSES = {
    "groq": GroqProvider,
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
}

# Extra constructor arguments each backend needs, beyond the model.
_PROVIDER_KWARGS = {
    "groq": {},
    "ollama": {"base_url": OLLAMA_BASE_URL},
    "openai": {},
}


def get_llm_provider() -> LLMProvider:
    """Return the configured provider, constructed with the configured model.

    The returned object is cheap to build but not free, so callers should hold
    onto it for repeated extractions rather than rebuilding per document.

    Raises:
        UnsupportedProviderError: LLM_PROVIDER names an unknown backend.
        MissingApiKeyError: the selected provider has no API key configured.
        ModelMismatchError: the configured model id is another provider's.
    """
    provider_class = _PROVIDER_CLASSES.get(LLM_PROVIDER)
    if provider_class is None:
        raise UnsupportedProviderError(
            f"Unsupported LLM_PROVIDER: {LLM_PROVIDER!r}. "
            f"Expected one of: {', '.join(sorted(_PROVIDER_CLASSES))}."
        )
    try:
        model = resolve_model(LLM_PROVIDER)
    except ModelConfigError as error:
        raise ModelMismatchError(str(error)) from error
    return provider_class(model=model, **_PROVIDER_KWARGS[LLM_PROVIDER])


__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "ProviderCapabilities",
    "ProviderError",
    "MissingApiKeyError",
    "ModelMismatchError",
    "UnsupportedProviderError",
    "EmptyResponseError",
    "GroqProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "get_llm_provider",
]
