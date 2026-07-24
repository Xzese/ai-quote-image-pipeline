import concurrent.futures
from types import SimpleNamespace

import pytest
import requests

from quote_image_generator import get_prompt


class FakeResponse:
    def __init__(self, body, status_error=None):
        self.body = body
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_call_model_includes_reasoning_effort_and_thinking_directives():
    captured = {}

    def fake_create(
        model,
        messages,
        temperature,
        stream,
        max_tokens,
        timeout,
        extra_body,
        reasoning_effort,
        response_format,
    ):
        captured["model"] = model
        captured["reasoning_effort"] = reasoning_effort
        captured["extra_body"] = extra_body
        captured["response_format"] = response_format

        message = SimpleNamespace(content='{"status":"ok"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_completions = SimpleNamespace(create=fake_create)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))

    result = get_prompt.call_model(
        messages=[{"role": "user", "content": "Hi"}],
        client=fake_client,
        model_name="qwen/qwen3.5-9b",
        preset="@local:no-thinking",
        response_format=get_prompt.READINESS_RESPONSE_FORMAT,
        expected_field="status",
        expected_type=str,
    )

    assert result == {"status": "ok"}
    assert captured["reasoning_effort"] == "none"
    assert captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert captured["extra_body"]["preset"] == "@local:no-thinking"
    assert captured["response_format"] == get_prompt.READINESS_RESPONSE_FORMAT


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"other":"ok"}', "missing 'status'"),
        ('{"status":1}', "wrong type"),
    ],
)
def test_call_model_rejects_invalid_structured_content(content, message):
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion)
        )
    )

    with pytest.raises(RuntimeError, match=message):
        get_prompt.call_model(
            messages=[],
            client=client,
            model_name="model",
            preset="",
            response_format=get_prompt.READINESS_RESPONSE_FORMAT,
            expected_field="status",
            expected_type=str,
        )


def test_generate_hashtags_rejects_more_than_maximum(monkeypatch):
    item = {"content": "Some quote", "author": "Author"}
    hashtags = " ".join(
        f"#tag{index}" for index in range(1, get_prompt.MAX_HASHTAGS + 2)
    )

    attempts = 0

    def fake_call_model(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return {"hashtags": hashtags.split()}

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
    assert settings["native_api_base_url"] == "http://example.test/api/v1"
    assert settings["api_key"] == "test-key"
    assert settings["context_length"] == 8192
    assert settings["parallel_workers"] == 3
    assert settings["quotes_file_path"] == quotes_path


def test_load_prompt_settings_normalizes_model_url_to_key(monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", "quotes.json")
    monkeypatch.setenv(
        "LM_STUDIO_MODEL",
        "https://lmstudio.ai/models/liquid/lfm2.5-1.2b/?foo=bar#baz",
    )
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    settings = get_prompt._load_prompt_settings()

    assert settings["model_name"] == "liquid/lfm2.5-1.2b"


def test_load_prompt_settings_uses_explicit_native_url_and_context(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QUOTES_FILE_PATH", str(tmp_path / "quotes.json"))
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://openai.test/v1")
    monkeypatch.setenv("LM_STUDIO_NATIVE_API_BASE_URL", "http://native.test/api/v1/")
    monkeypatch.setenv("LM_STUDIO_CONTEXT_LENGTH", "4096")
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    settings = get_prompt._load_prompt_settings()

    assert settings["native_api_base_url"] == "http://native.test/api/v1"
    assert settings["context_length"] == 4096


def test_normalize_model_name_removes_lmstudio_web_prefix_with_quantization():
    assert (
        get_prompt._normalize_model_name(
            "https://lmstudio.ai/models/liquid/lfm2.5-1.2b@q4_k_m/?version=1#readme"
        )
        == "liquid/lfm2.5-1.2b@q4_k_m"
    )


def test_normalize_model_name_preserves_plain_model_key():
    assert get_prompt._normalize_model_name("qwen/qwen3.5-9b") == "qwen/qwen3.5-9b"


def test_normalize_model_name_rejects_non_matching_urls():
    with pytest.raises(
        get_prompt.ConfigurationError,
        match="is a URL but is not an LM Studio model page URL",
    ):
        get_prompt._normalize_model_name(
            "https://example.com/models/liquid/lfm2.5-1.2b"
        )


def test_normalize_model_name_rejects_unapproved_subdomain():
    with pytest.raises(
        get_prompt.ConfigurationError,
        match="is a URL but is not an LM Studio model page URL",
    ):
        get_prompt._normalize_model_name(
            "https://api.lmstudio.ai/models/liquid/lfm2.5-1.2b"
        )


def test_normalize_model_name_rejects_wrong_path():
    with pytest.raises(
        get_prompt.ConfigurationError,
        match="LM_STUDIO_MODEL URL format is invalid",
    ):
        get_prompt._normalize_model_name(
            "https://lmstudio.ai/library/liquid/lfm2.5-1.2b"
        )


def test_normalize_model_name_rejects_malformed_model_path():
    with pytest.raises(
        get_prompt.ConfigurationError,
        match="LM_STUDIO_MODEL URL format is invalid",
    ):
        get_prompt._normalize_model_name("https://lmstudio.ai/models/liquid")


def test_load_prompt_settings_rejects_non_matching_model_urls(monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", "quotes.json")
    monkeypatch.setenv(
        "LM_STUDIO_MODEL", "https://example.com/models/liquid/lfm2.5-1.2b"
    )
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    with pytest.raises(
        get_prompt.ConfigurationError,
        match="is a URL but is not an LM Studio model page URL",
    ):
        get_prompt._load_prompt_settings()


def test_load_prompt_settings_rejects_invalid_context_length(monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", "quotes.json")
    monkeypatch.setenv("LM_STUDIO_CONTEXT_LENGTH", "0")
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    with pytest.raises(get_prompt.ConfigurationError, match="must be at least 1"):
        get_prompt._load_prompt_settings()


def test_load_prompt_settings_rejects_invalid_parallel_workers(monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", "quotes.json")
    monkeypatch.setenv("LM_STUDIO_PARALLEL_WORKERS", "0")
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    with pytest.raises(get_prompt.ConfigurationError, match="must be at least 1"):
        get_prompt._load_prompt_settings()


def test_ensure_model_leaves_loaded_instance_unchanged():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "models": [
                        {
                            "key": "model",
                            "loaded_instances": [{"id": "loaded"}],
                            "max_context_length": 16384,
                        }
                    ]
                }
            )
        ]
    )

    get_prompt.ensure_lm_studio_model(
        native_api_base_url="http://localhost:1234/api/v1",
        api_key="key",
        model_name="model",
        context_length=8192,
        session=session,
    )

    assert len(session.calls) == 1
    assert session.calls[0][0:2] == (
        "GET",
        "http://localhost:1234/api/v1/models",
    )
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer key"


def test_ensure_model_uses_normalized_name_for_listing_and_does_not_download():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "models": [
                        {
                            "key": "liquid/lfm2.5-1.2b",
                            "loaded_instances": [{"id": "preloaded"}],
                            "max_context_length": 4096,
                        }
                    ]
                }
            )
        ]
    )

    result = get_prompt.ensure_lm_studio_model(
        native_api_base_url="http://localhost/api/v1",
        api_key="key",
        model_name="https://lmstudio.ai/models/liquid/lfm2.5-1.2b/?foo=bar#baz",
        context_length=2048,
        session=session,
    )

    assert result == "preloaded"
    assert session.calls[0][0] == "GET"
    assert session.calls[0][1] == "http://localhost/api/v1/models"
    assert all("/models/download" not in call[1] for call in session.calls)
    assert all("/models/load" not in call[1] for call in session.calls)


@pytest.mark.parametrize(
    ("max_context", "expected_context"),
    [(32768, 8192), (4096, 4096)],
)
def test_ensure_model_downloads_polls_and_loads_with_bounded_context(
    max_context, expected_context, monkeypatch
):
    monkeypatch.setattr(get_prompt.stop_event, "wait", lambda _seconds: False)
    model = {
        "key": "model",
        "loaded_instances": [],
        "max_context_length": max_context,
    }
    session = FakeSession(
        [
            FakeResponse({"models": []}),
            FakeResponse({"status": "downloading", "job_id": "job-1"}),
            FakeResponse(
                {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_size_bytes": 100,
                }
            ),
            FakeResponse({"status": "completed"}),
            FakeResponse({"models": [model]}),
            FakeResponse({"instance_id": "loaded"}),
        ]
    )

    loaded_instance_id = get_prompt.ensure_lm_studio_model(
        native_api_base_url="http://localhost:1234/api/v1",
        api_key="key",
        model_name="model",
        context_length=8192,
        session=session,
    )

    assert session.calls[1][2]["json"] == {"model": "model"}
    assert session.calls[2][1].endswith("/models/download/status/job-1")
    assert session.calls[-1][1].endswith("/models/load")
    assert session.calls[-1][2]["json"] == {
        "model": "model",
        "context_length": expected_context,
    }
    assert loaded_instance_id == "loaded"


def test_ensure_model_handles_already_downloaded_then_loads():
    model = {
        "key": "model",
        "loaded_instances": [],
        "max_context_length": 8192,
    }
    session = FakeSession(
        [
            FakeResponse({"models": []}),
            FakeResponse({"status": "already_downloaded"}),
            FakeResponse({"models": [model]}),
            FakeResponse({"instance_id": "loaded"}),
        ]
    )

    get_prompt.ensure_lm_studio_model(
        native_api_base_url="http://localhost/api/v1",
        api_key="key",
        model_name="model",
        context_length=8192,
        session=session,
    )

    assert session.calls[-1][1].endswith("/models/load")


def test_ensure_model_downloaded_but_not_in_list_falls_back_to_direct_load(monkeypatch):
    monkeypatch.setattr(get_prompt, "MODEL_LIST_WAIT_RETRIES", 1)
    monkeypatch.setattr(get_prompt.stop_event, "wait", lambda _seconds: False)
    session = FakeSession(
        [
            FakeResponse({"models": []}),
            FakeResponse({"status": "already_downloaded"}),
            FakeResponse({"models": []}),
            FakeResponse({"instance_id": "loaded-direct"}),
        ]
    )

    loaded_instance_id = get_prompt.ensure_lm_studio_model(
        native_api_base_url="http://localhost/api/v1",
        api_key="key",
        model_name="model",
        context_length=8192,
        session=session,
    )

    assert loaded_instance_id == "loaded-direct"
    assert session.calls[-1][1].endswith("/models/load")
    assert session.calls[-1][2]["json"] == {
        "model": "model",
        "context_length": 8192,
    }


@pytest.mark.parametrize("status", ["failed", "paused", "mystery"])
def test_ensure_model_rejects_terminal_or_unknown_download_status(status):
    session = FakeSession(
        [
            FakeResponse({"models": []}),
            FakeResponse({"status": status, "message": "detail"}),
        ]
    )

    with pytest.raises(RuntimeError, match="download"):
        get_prompt.ensure_lm_studio_model(
            native_api_base_url="http://localhost/api/v1",
            api_key="key",
            model_name="model",
            context_length=8192,
            session=session,
        )


def test_unload_lm_studio_model_posts_instance_id():
    session = FakeSession([FakeResponse({"instance_id": "loaded"})])

    get_prompt._unload_lm_studio_model(
        session=session,
        native_api_base_url="http://localhost/api/v1",
        api_key="key",
        instance_id="loaded",
    )

    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/models/unload")
    assert session.calls[0][2]["json"] == {"instance_id": "loaded"}


def test_load_lm_studio_model_accepts_model_instance_id_field():
    session = FakeSession([FakeResponse({"model_instance_id": "loaded"})])

    instance_id = get_prompt._load_lm_studio_model(
        session=session,
        native_api_base_url="http://localhost/api/v1",
        api_key="key",
        model_name="model",
        context_length=2048,
    )

    assert instance_id == "loaded"
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/models/load")
    assert session.calls[0][2]["json"] == {"model": "model", "context_length": 2048}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeResponse(ValueError("bad json")), "malformed JSON"),
        (
            FakeResponse({}, requests.HTTPError("500 Server Error")),
            "native API request failed",
        ),
    ],
)
def test_ensure_model_reports_malformed_and_http_errors(response, message):
    with pytest.raises(RuntimeError, match=message):
        get_prompt.ensure_lm_studio_model(
            native_api_base_url="http://localhost/api/v1",
            api_key="key",
            model_name="model",
            context_length=8192,
            session=FakeSession([response]),
        )


def test_generate_prompt_consumes_structured_prompt(monkeypatch):
    monkeypatch.setattr(
        get_prompt, "call_model", lambda *_, **__: {"prompt": "misty forest at dawn"}
    )
    tokenizer = SimpleNamespace(tokenize=lambda value: value.split())

    result = get_prompt.generate_prompt(
        {"content": "Quote", "author": "Author"},
        0,
        client=object(),
        tokenizer=tokenizer,
        model_name="model",
        preset="",
    )

    assert result == "misty forest at dawn"


def test_generate_prompt_rejects_prompt_with_double_quotes(monkeypatch):
    attempts = 0

    def fake_call_model(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return {"prompt": 'quoted text "should" be rejected'}

    monkeypatch.setattr(get_prompt, "call_model", fake_call_model)
    tokenizer = SimpleNamespace(tokenize=lambda value: value.split())

    result = get_prompt.generate_prompt(
        {"content": "Quote", "author": "Author"},
        0,
        client=object(),
        tokenizer=tokenizer,
        model_name="model",
        preset="",
    )

    assert result is None
    assert attempts == get_prompt.MAX_PROMPT_RETRIES


def test_generate_hashtags_consumes_and_normalizes_structured_array(monkeypatch):
    monkeypatch.setattr(
        get_prompt,
        "call_model",
        lambda *_, **__: {"hashtags": [" #Wisdom ", "#AIArt", "#wisdom", "#Quotes"]},
    )

    result = get_prompt.generate_hashtags(
        {"content": "Quote", "author": "Author"},
        0,
        client=object(),
        model_name="model",
        preset="",
    )

    assert result == "#Wisdom #AIArt #Quotes"


def test_generate_hashtags_rejects_unrelated_text(monkeypatch):
    attempts = 0

    def fake_call_model(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return {"hashtags": ["not a hashtag"]}

    monkeypatch.setattr(get_prompt, "call_model", fake_call_model)

    result = get_prompt.generate_hashtags(
        {"content": "Quote", "author": "Author"},
        0,
        client=object(),
        model_name="model",
        preset="",
    )

    assert result is None
    assert attempts == get_prompt.MAX_HASHTAG_RETRIES


def test_validate_lm_studio_readiness_rejects_structured_output_failure(monkeypatch):
    attempts = 0

    def fail(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("not valid JSON")

    monkeypatch.setattr(get_prompt, "call_model", fail)

    with pytest.raises(RuntimeError, match="structured-output check failed"):
        get_prompt._validate_lm_studio_readiness(
            client=object(),
            model_name="qwen/qwen3.5-9b",
            preset="",
        )

    assert attempts == 1


def test_validate_lm_studio_readiness_retries_then_succeeds(monkeypatch):
    attempts = 0
    wait_calls = []

    def fail_then_succeed(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(
                "LM Studio request failed for model 'qwen/qwen3.5-9b': has been unloaded."
            )
        return {"status": "ok"}

    monkeypatch.setattr(get_prompt, "call_model", fail_then_succeed)
    monkeypatch.setattr(
        get_prompt.stop_event,
        "wait",
        lambda _seconds: wait_calls.append(_seconds) or False,
    )

    get_prompt._validate_lm_studio_readiness(
        client=object(),
        model_name="qwen/qwen3.5-9b",
        preset="",
    )

    assert attempts == get_prompt.MODEL_READINESS_RETRY_ATTEMPTS
    assert wait_calls == [get_prompt.MODEL_READINESS_RETRY_DELAY_SECONDS] * (
        get_prompt.MODEL_READINESS_RETRY_ATTEMPTS - 1
    )


def test_validate_lm_studio_readiness_retries_then_fails_after_three_attempts(
    monkeypatch,
):
    attempts = 0
    wait_calls = []

    def always_stale(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError(
            "LM Studio request failed for model 'qwen/qwen3.5-9b': has been unloaded."
        )

    monkeypatch.setattr(get_prompt, "call_model", always_stale)
    monkeypatch.setattr(
        get_prompt.stop_event,
        "wait",
        lambda _seconds: wait_calls.append(_seconds) or False,
    )

    with pytest.raises(RuntimeError, match="structured-output check failed"):
        get_prompt._validate_lm_studio_readiness(
            client=object(),
            model_name="qwen/qwen3.5-9b",
            preset="",
        )

    assert attempts == 3
    assert wait_calls == [1, 1]


def test_validate_lm_studio_readiness_cancelled_during_retry_wait(monkeypatch):
    attempts = 0

    def stale_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError(
            "LM Studio request failed for model 'qwen/qwen3.5-9b': has been unloaded."
        )

    monkeypatch.setattr(get_prompt, "call_model", stale_once)
    monkeypatch.setattr(get_prompt.stop_event, "wait", lambda _seconds: True)

    with pytest.raises(
        InterruptedError, match="while waiting to retry readiness check"
    ):
        get_prompt._validate_lm_studio_readiness(
            client=object(),
            model_name="qwen/qwen3.5-9b",
            preset="",
        )

    assert attempts == 1


def test_validate_lm_studio_readiness_normalizes_model_page_url(monkeypatch):
    observed = {}

    def fake_call_model(*_args, **kwargs):
        observed["model_name"] = kwargs["model_name"]
        return {"status": "ok"}

    monkeypatch.setattr(get_prompt, "call_model", fake_call_model)

    get_prompt._validate_lm_studio_readiness(
        client=object(),
        model_name="https://lmstudio.ai/models/liquid/lfm2.5-1.2b?foo=bar#baz",
        preset="",
    )

    assert observed["model_name"] == "liquid/lfm2.5-1.2b"


def test_main_injects_client_without_external_calls(monkeypatch, tmp_path):
    expected_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(expected_path))
    monkeypatch.delenv("LM_STUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)
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
    monkeypatch.setattr(
        get_prompt, "_validate_lm_studio_readiness", lambda *_, **__: None
    )
    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", lambda **_: None)
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


def test_main_reports_clean_startup_cancellation(monkeypatch, tmp_path):
    quotes_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_path))
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)
    monkeypatch.setattr(
        get_prompt,
        "_load_quote_data",
        lambda _path: [{"content": "c", "author": "a", "prompt": "", "hashtags": ""}],
    )

    def cancel_startup(**_kwargs):
        get_prompt.stop_event.set()
        raise InterruptedError("Shutdown requested while downloading LM Studio model")

    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", cancel_startup)
    logs = []
    monkeypatch.setattr(get_prompt, "log", logs.append)

    result = get_prompt.main()

    assert result == 130
    assert logs == ["Startup cancelled. No quote items were processed."]


def test_main_reports_single_pending_cancel_summary(monkeypatch, tmp_path):
    quotes_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_path))
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)
    monkeypatch.setattr(
        get_prompt,
        "_load_quote_data",
        lambda path: [
            {"content": f"c{idx}", "author": "a", "prompt": "", "hashtags": ""}
            for idx in range(3)
        ],
    )
    monkeypatch.setattr(get_prompt, "create_tokenizer", lambda: object())
    monkeypatch.setattr(get_prompt, "create_openai_client", lambda *_: object())
    monkeypatch.setattr(
        get_prompt,
        "_validate_lm_studio_readiness",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", lambda **_: None)
    monkeypatch.setattr(get_prompt, "save_json", lambda *_args, **_kwargs: None)

    class PendingOnlyExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, *_args, **_kwargs):
            get_prompt.stop_event.set()
            return concurrent.futures.Future()

        def shutdown(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(get_prompt, "ThreadPoolExecutor", PendingOnlyExecutor)

    logs = []
    monkeypatch.setattr(get_prompt, "log", lambda message: logs.append(message))

    result = get_prompt.main()

    pending_cancel_lines = [
        line for line in logs if "pending items have been cancelled" in line
    ]
    pending_cancel_lines += [
        line for line in logs if "pending item has been cancelled" in line
    ]

    assert result == 0
    assert len(pending_cancel_lines) == 1
    assert all("Cancelled pending Item" not in line for line in logs)


def test_main_does_not_emit_per_item_cancel_logs(monkeypatch, tmp_path):
    quotes_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_path))
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)
    monkeypatch.setattr(
        get_prompt,
        "_load_quote_data",
        lambda path: [
            {"content": f"c{idx}", "author": "a", "prompt": "", "hashtags": ""}
            for idx in range(3)
        ],
    )
    monkeypatch.setattr(get_prompt, "create_tokenizer", lambda: object())
    monkeypatch.setattr(get_prompt, "create_openai_client", lambda *_: object())
    monkeypatch.setattr(
        get_prompt,
        "_validate_lm_studio_readiness",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", lambda **_: None)
    monkeypatch.setattr(get_prompt, "save_json", lambda *_args, **_kwargs: None)

    class ShutdownFuturesExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, *_args, **_kwargs):
            get_prompt.stop_event.set()
            future = concurrent.futures.Future()
            future.set_exception(InterruptedError("Shutdown requested"))
            return future

        def shutdown(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(get_prompt, "ThreadPoolExecutor", ShutdownFuturesExecutor)

    logs = []
    monkeypatch.setattr(get_prompt, "log", lambda message: logs.append(message))

    result = get_prompt.main()

    assert result == 0
    assert all("Stopped Item" not in line for line in logs)
    assert all("Finished Item" not in line for line in logs)


def test_main_second_ctrl_c_exits_immediately(monkeypatch, tmp_path):
    quotes_path = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_path))
    monkeypatch.setattr(get_prompt, "load_project_env", lambda: None)

    monkeypatch.setattr(
        get_prompt,
        "_load_quote_data",
        lambda path: [{"content": "c", "author": "a", "prompt": "", "hashtags": ""}],
    )
    monkeypatch.setattr(get_prompt, "create_tokenizer", lambda: object())
    monkeypatch.setattr(get_prompt, "create_openai_client", lambda *_: object())
    monkeypatch.setattr(
        get_prompt,
        "_validate_lm_studio_readiness",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", lambda **_: None)

    save_calls = []
    monkeypatch.setattr(
        get_prompt,
        "save_json",
        lambda *_args, **_kwargs: save_calls.append("saved"),
    )

    shutdown_calls = []

    class ImmediateCancelExecutor:
        def __init__(self, *_args, **_kwargs):
            pass

        def submit(self, *_args, **_kwargs):
            get_prompt.stop_event.set()
            get_prompt.force_exit_event.set()
            return concurrent.futures.Future()

        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)

    monkeypatch.setattr(get_prompt, "ThreadPoolExecutor", ImmediateCancelExecutor)

    result = get_prompt.main()

    assert result == 130
    assert save_calls == []
    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


class _ImmediateExit(SystemExit):
    pass


def test_handle_sigint_second_catches_exit_immediately(monkeypatch):
    get_prompt.stop_event.clear()
    get_prompt.force_exit_event.clear()

    logs = []
    exit_calls = []

    monkeypatch.setattr(get_prompt, "log", lambda message: logs.append(message))

    def fake_exit(code: int = 0):
        exit_calls.append(code)
        raise _ImmediateExit(code)

    monkeypatch.setattr(get_prompt.os, "_exit", fake_exit)

    get_prompt.handle_sigint(2, None)
    assert get_prompt.stop_event.is_set()
    assert not exit_calls

    with pytest.raises(_ImmediateExit):
        get_prompt.handle_sigint(2, None)

    assert exit_calls == [130]
    assert logs.count("\nSecond Ctrl+C received. Exiting immediately.") == 1
