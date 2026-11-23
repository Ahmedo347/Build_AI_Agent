from argparse import Namespace

import pytest

from settings import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings


def args(**overrides) -> Namespace:
    base = {
        "base_url": None,
        "model": None,
        "min_interval": None,
        "working_directory": "calculator",
        "verbose": False,
    }
    base.update(overrides)
    return Namespace(**base)


def test_defaults():
    settings = Settings.from_env(args(), environ={})

    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.model == DEFAULT_MODEL
    assert settings.api_key == ""
    assert settings.min_interval == 4.0
    assert settings.max_tokens == 8192


def test_env_wins_over_defaults():
    settings = Settings.from_env(
        args(), environ={"LLM_API_KEY": "k", "LLM_MODEL": "m", "LLM_MIN_INTERVAL": "7"}
    )

    assert (settings.api_key, settings.api_key_source) == ("k", "LLM_API_KEY")
    assert settings.model == "m"
    assert settings.min_interval == 7.0


def test_cli_wins_over_env():
    settings = Settings.from_env(
        args(model="cli", min_interval=9.0),
        environ={"LLM_MODEL": "env", "LLM_MIN_INTERVAL": "1"},
    )

    assert settings.model == "cli"
    assert settings.min_interval == 9.0


def test_base_url_gets_trailing_slash():
    settings = Settings.from_env(
        args(), environ={"LLM_BASE_URL": "http://localhost:3001/v1"}
    )

    assert settings.base_url == "http://localhost:3001/v1/"


def test_openrouter_backwards_compatibility():
    settings = Settings.from_env(
        args(),
        environ={
            "LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY": "sk-or",
            "OPENROUTER_MODEL": "cohere/x:free",
        },
    )

    assert settings.api_key_source == "OPENROUTER_API_KEY"
    assert settings.model == "cohere/x:free"


def test_openrouter_key_never_sent_to_another_provider():
    settings = Settings.from_env(args(), environ={"OPENROUTER_API_KEY": "sk-or"})

    assert settings.api_key == ""
    assert settings.api_key_source == ""


def test_provider_specific_key_used_for_its_own_base_url():
    settings = Settings.from_env(
        args(),
        environ={
            "LLM_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "OPENROUTER_API_KEY": "sk-or",
            "GOOGLE_API_KEY": "AIza",
        },
    )

    assert settings.api_key == "AIza"
    assert settings.api_key_source == "GOOGLE_API_KEY"


def test_freellmapi_key_supported():
    settings = Settings.from_env(
        args(),
        environ={
            "LLM_BASE_URL": "http://localhost:3001/v1",
            "FREELLMAPI_API_KEY": "freellmapi-x",
        },
    )

    assert settings.api_key == "freellmapi-x"


def test_generic_key_beats_provider_specific():
    settings = Settings.from_env(
        args(),
        environ={
            "LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "LLM_API_KEY": "generic",
            "OPENROUTER_API_KEY": "specific",
        },
    )

    assert settings.api_key == "generic"


@pytest.mark.parametrize("raw", ["not-a-number", ""])
def test_bad_numeric_env_falls_back_to_default(raw):
    settings = Settings.from_env(args(), environ={"LLM_MIN_INTERVAL": raw})

    assert settings.min_interval == 4.0


def test_missing_key_hint_is_actionable():
    settings = Settings.from_env(args(), environ={})

    assert "LLM_API_KEY" in settings.missing_key_hint
    assert settings.base_url in settings.missing_key_hint
