from __future__ import annotations

import pytest

from relational_schema_analyzer import list_providers, register_provider
from relational_schema_analyzer.defaults import DEFAULT_OPENAI_MODEL
from relational_schema_analyzer.providers import (
    create_provider,
    get_default_model,
    get_provider_env_var,
)
from relational_schema_analyzer.providers.base import LLMError, import_optional_sdk


def test_builtin_providers_listed():
    assert {"openai", "anthropic", "openrouter"} <= set(list_providers())


def test_default_model_and_env_var():
    assert get_default_model("openai") == DEFAULT_OPENAI_MODEL
    assert get_provider_env_var("openai") == "OPENAI_API_KEY"
    assert get_provider_env_var("nope") is None


def test_unknown_provider_raises():
    with pytest.raises(LLMError):
        get_default_model("nope")
    with pytest.raises(LLMError):
        create_provider("nope", api_key="x")


def test_register_custom_provider():
    register_provider(
        "custom", module="x.y", class_name="Z", env_var="CUSTOM_KEY", default_model="m"
    )
    assert "custom" in list_providers()
    assert get_default_model("custom") == "m"


def test_import_optional_sdk_missing():
    with pytest.raises(LLMError) as exc:
        import_optional_sdk("no_such_sdk_xyz", "no_such_sdk_xyz")
    assert exc.value.code == "PROVIDER_MISSING"
