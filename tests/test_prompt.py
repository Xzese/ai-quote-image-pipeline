import concurrent.futures

from quote_image_generator import get_prompt
import pytest


def test_extract_braced_content_prefers_braces_when_present():
    text = "prefix {first} and {second}"

    assert get_prompt.extract_braced_content(text) == "first"


def test_extract_fallback_text_removes_markdown_and_labels():
    text = '```json\nprompt: "a minimalist mountain sunrise"\n```'

    assert get_prompt.extract_fallback_text(text) == "a minimalist mountain sunrise"


def test_extract_fallback_text_returns_none_when_empty():
    assert get_prompt.extract_fallback_text("   \n```") is None


def test_generate_hashtags_rejects_more_than_maximum(monkeypatch):
    item = {"content": "Some quote", "author": "Author"}
    hashtags = " ".join(
        f"#tag{index}" for index in range(1, get_prompt.MAX_HASHTAGS + 2)
    )

    attempts = 0

    def fake_call_model(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return f"{{{hashtags}}}"

    monkeypatch.setattr(get_prompt, "call_model", fake_call_model)

    result = get_prompt.generate_hashtags(
        item, 0, client=object(), model_name="x", preset="y"
    )

    assert result is None
    assert attempts == get_prompt.MAX_HASHTAG_RETRIES


def test_load_prompt_settings_uses_defaults_and_env_overrides(tmp_path, monkeypatch):
    quotes_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_path))
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "test-key")
    monkeypatch.setenv("LM_STUDIO_PARALLEL_WORKERS", "3")
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    settings = get_prompt._load_prompt_settings()

    assert settings["base_url"] == "http://example.test/v1"
    assert settings["api_key"] == "test-key"
    assert settings["parallel_workers"] == 3
    assert settings["quotes_file_path"] == quotes_path


def test_load_prompt_settings_rejects_invalid_parallel_workers(monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", "quotes.json")
    monkeypatch.setenv("LM_STUDIO_PARALLEL_WORKERS", "0")
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    with pytest.raises(get_prompt.ConfigurationError, match="must be at least 1"):
        get_prompt._load_prompt_settings()


def test_main_injects_client_without_external_calls(monkeypatch, tmp_path):
    expected_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(expected_path))
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    monkeypatch.setattr(
        get_prompt,
        "_load_quote_data",
        lambda path: [
            {"_id": "id", "content": "c", "author": "a", "prompt": "", "hashtags": ""}
        ],
    )
    monkeypatch.setattr(
        get_prompt, "process_item", lambda *_args, **_kwargs: (0, False)
    )
    monkeypatch.setattr(get_prompt, "create_tokenizer", lambda: object())

    created = {}

    def fake_create_client(base_url: str, api_key: str):
        created.update({"base_url": base_url, "api_key": api_key})
        return object()

    monkeypatch.setattr(get_prompt, "create_openai_client", fake_create_client)

    class ImmediateExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, fn, *args, **kwargs):
            future = concurrent.futures.Future()
            future.set_result(fn(*args, **kwargs))
            return future

        def shutdown(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(get_prompt, "ThreadPoolExecutor", ImmediateExecutor)
    monkeypatch.setattr(get_prompt, "save_json", lambda *_args, **_kwargs: None)

    result = get_prompt.main()

    assert result == 0
    assert created["base_url"] == get_prompt.DEFAULT_LM_STUDIO_BASE_URL
    assert created["api_key"] == get_prompt.DEFAULT_LM_STUDIO_API_KEY
