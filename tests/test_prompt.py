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


def test_prompt_main_persists_complete_receipt(monkeypatch, tmp_path):
    import json
    from quote_image_generator.pipeline import LivePrompt

    path = tmp_path / "quotes.json"
    path.write_text(json.dumps([{"_id": "id", "content": "c", "author": "a"}]))
    monkeypatch.setenv("QUOTES_FILE_PATH", str(path))
    monkeypatch.setattr(
        LivePrompt,
        "__call__",
        lambda self, record: {"prompt": "blue sky", "hashtags": "#sky"},
    )
    assert get_prompt.main() == 0
    receipt = json.loads((tmp_path / "quotes.json.state.json").read_text())
    assert receipt["items"]["id"]["prompt"]["status"] == "completed"
    assert (
        receipt["items"]["id"]["prompt"]["metadata"]["measurement"]
        == "whitespace-words-v1"
    )


def test_prompt_main_reports_generation_failure(monkeypatch, tmp_path):
    import json
    from quote_image_generator.pipeline import LivePrompt

    path = tmp_path / "quotes.json"
    path.write_text(json.dumps([{"_id": "id", "content": "c", "author": "a"}]))
    monkeypatch.setenv("QUOTES_FILE_PATH", str(path))
    monkeypatch.setattr(
        LivePrompt,
        "__call__",
        lambda self, record: {"prompt": None, "hashtags": "#sky"},
    )
    assert get_prompt.main() == 1
    assert (
        json.loads((tmp_path / "quotes.json.summary.json").read_text())["counts"][
            "failed"
        ]
        == 1
    )


def test_prompt_main_reports_startup_cancellation(monkeypatch, tmp_path):
    import json

    path = tmp_path / "quotes.json"
    path.write_text(json.dumps([{"_id": "id", "content": "c", "author": "a"}]))
    monkeypatch.setenv("QUOTES_FILE_PATH", str(path))

    def cancel(**kwargs):
        raise InterruptedError("cancelled")

    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", cancel)
    assert get_prompt.main() == 130


def test_live_prompt_unloads_owned_model_after_readiness_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("QUOTES_FILE_PATH", str(tmp_path / "corpus.json"))
    from quote_image_generator.pipeline import LivePrompt

    def ensure(**kwargs):
        kwargs["on_load"]("owned")
        return "owned"

    monkeypatch.setattr(get_prompt, "ensure_lm_studio_model", ensure)
    monkeypatch.setattr(get_prompt, "create_openai_client", lambda *_: object())
    monkeypatch.setattr(
        get_prompt,
        "_validate_lm_studio_readiness",
        lambda **_: (_ for _ in ()).throw(RuntimeError("not ready")),
    )
    unloaded = []
    monkeypatch.setattr(
        get_prompt,
        "_unload_lm_studio_model",
        lambda **kw: unloaded.append(kw["instance_id"]),
    )
    adapter = LivePrompt()
    adapter.metadata()
    with pytest.raises(RuntimeError):
        adapter({"_id": "id", "content": "c", "author": "a"})
    adapter.close()
    assert unloaded == ["owned"]


def test_word_count_is_local_and_deterministic():
    counter = get_prompt.create_tokenizer()
    assert counter.tokenize("  après la pluie — lumière  ") == [
        "après",
        "la",
        "pluie",
        "—",
        "lumière",
    ]


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


def test_live_prompt_never_unloads_reused_instance(monkeypatch, tmp_path):
    from quote_image_generator.pipeline import LivePrompt

    monkeypatch.setenv("QUOTES_FILE_PATH", str(tmp_path / "corpus.json"))
    monkeypatch.setattr(
        get_prompt, "ensure_lm_studio_model", lambda **_: "pre-existing"
    )
    monkeypatch.setattr(get_prompt, "create_openai_client", lambda *_: object())
    monkeypatch.setattr(get_prompt, "_validate_lm_studio_readiness", lambda **_: None)
    monkeypatch.setattr(get_prompt, "generate_prompt", lambda *_, **__: "sky")
    monkeypatch.setattr(get_prompt, "generate_hashtags", lambda *_, **__: "#sky")
    unloaded = []
    monkeypatch.setattr(
        get_prompt,
        "_unload_lm_studio_model",
        lambda **kw: unloaded.append(kw["instance_id"]),
    )
    adapter = LivePrompt()
    adapter.metadata()
    adapter({"_id": "id", "content": "c", "author": "a"})
    adapter.close()
    assert unloaded == []
