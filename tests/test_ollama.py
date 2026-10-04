import json
import threading
from unittest.mock import Mock

import pytest
import requests

from quote_image_generator import get_prompt, ollama
from quote_image_generator.config import ConfigurationError
from quote_image_generator.pipeline import LivePrompt, run_workflow


@pytest.fixture(autouse=True)
def clear_stop():
    get_prompt.stop_event.clear()
    yield
    get_prompt.stop_event.clear()


def response(body):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(body).encode()
    return result


def test_native_schema_chat_and_thinking_capability(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/api/show"):
            return response({"capabilities": ["completion", "thinking"]})
        return response(
            {
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"status":"ok"}'},
            }
        )

    monkeypatch.setattr(ollama, "request", request)
    stop = threading.Event()
    client = ollama.OllamaClient("http://localhost:11434/", 2048, stop)
    for _ in range(2):
        assert get_prompt.call_model(
            [{"role": "user", "content": "ready?"}],
            client=client,
            model_name="qwen3:0.6b",
            preset="unused-lm-preset",
            response_format=get_prompt.READINESS_RESPONSE_FORMAT,
            expected_field="status",
            expected_type=str,
        ) == {"status": "ok"}
    assert len(calls) == 3  # Model capabilities are checked once.
    payload = calls[1][2]["json"]
    assert (
        payload["format"]
        == get_prompt.READINESS_RESPONSE_FORMAT["json_schema"]["schema"]
    )
    assert payload["stream"] is False and payload["think"] is False
    assert payload["options"] == {
        "temperature": 0.7,
        "num_ctx": 2048,
        "num_predict": 512,
    }
    assert calls[1][2]["stop_event"] is stop
    assert "extra_body" not in payload and "preset" not in payload
    client.close()
    assert len(calls) == 3  # Closing never unloads a shared server model.


def test_non_thinking_model_omits_think_and_embedding_is_rejected(monkeypatch):
    calls = []

    def request(method, url, **kw):
        calls.append(kw["json"])
        if url.endswith("/api/show"):
            return response({"capabilities": ["completion"]})
        return response({"done": True, "message": {"content": '{"status":"ok"}'}})

    monkeypatch.setattr(ollama, "request", request)
    client = ollama.OllamaClient("http://localhost:11434", 2048, threading.Event())
    client.call_structured(
        messages=[], model="small", response_format=get_prompt.READINESS_RESPONSE_FORMAT
    )
    assert "think" not in calls[-1]
    monkeypatch.setattr(
        ollama, "request", lambda *_, **__: response({"capabilities": ["embedding"]})
    )
    client = ollama.OllamaClient("http://localhost:11434", 2048, threading.Event())
    with pytest.raises(ValueError, match="chat/completion"):
        client.call_structured(
            messages=[],
            model="embedding",
            response_format=get_prompt.READINESS_RESPONSE_FORMAT,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"done": False, "message": {"content": '{"status":"ok"}'}},
        {
            "done": True,
            "done_reason": "length",
            "message": {"content": '{"status":"ok"}'},
        },
        {"done": True, "message": {}},
        {"done": True, "message": {"content": "not json"}},
        {"done": True, "message": {"content": '{"status":42}'}},
    ],
)
def test_bad_or_truncated_response_fails(body, monkeypatch):
    client = ollama.OllamaClient("http://localhost:11434", 2048, threading.Event())
    client.capabilities = ["completion"]
    monkeypatch.setattr(ollama, "request", lambda *_, **__: response(body))
    with pytest.raises(RuntimeError):
        get_prompt.call_model(
            [],
            client=client,
            model_name="small",
            preset="",
            response_format=get_prompt.READINESS_RESPONSE_FORMAT,
            expected_field="status",
            expected_type=str,
        )


def test_cancelled_readiness_remains_cancelled(monkeypatch):
    client = ollama.OllamaClient("http://localhost:11434", 2048, threading.Event())
    monkeypatch.setattr(ollama, "request", Mock(side_effect=InterruptedError("cancel")))
    with pytest.raises(InterruptedError):
        get_prompt._validate_lm_studio_readiness(
            client=client, model_name="qwen3:0.6b", preset=""
        )


@pytest.mark.parametrize("stage", ["prompt", "hashtags"])
def test_generation_cancellation_does_not_retry(stage, monkeypatch):
    call = Mock(side_effect=InterruptedError("cancel"))
    monkeypatch.setattr(get_prompt, "call_model", call)
    kwargs = {"client": object(), "model_name": "small", "preset": ""}
    if stage == "prompt":
        kwargs["tokenizer"] = get_prompt.create_tokenizer()
    with pytest.raises(InterruptedError):
        getattr(get_prompt, "generate_" + stage)(
            {"content": "Begin.", "author": "Fixture"}, "one", **kwargs
        )
    assert call.call_count == 1


@pytest.mark.parametrize(
    "loaded,should_unload",
    [
        ([], True),
        ([{"name": "small:latest"}], False),
        ([{"model": "small"}], False),
        ([{"name": "another:latest"}], True),
    ],
)
def test_batch_releases_only_model_not_previously_loaded(
    loaded, should_unload, monkeypatch
):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/api/show"):
            return response({"capabilities": ["completion"]})
        if url.endswith("/api/ps"):
            return response({"models": loaded})
        if url.endswith("/api/generate"):
            return response({"done": True, "done_reason": "unload"})
        return response({"done": True, "message": {"content": '{"status":"ok"}'}})

    monkeypatch.setattr(ollama, "request", request)
    stop = threading.Event()
    client = ollama.OllamaClient(
        "http://localhost:11434", 2048, stop, release_on_close=True
    )
    client.call_structured(
        messages=[], model="small", response_format=get_prompt.READINESS_RESPONSE_FORMAT
    )
    stop.set()  # Cleanup still runs after cancellation.
    client.close()
    client.close()
    unloads = [call for call in calls if call[1].endswith("/api/generate")]
    assert len(unloads) == int(should_unload)
    if unloads:
        assert unloads[0][2]["json"]["keep_alive"] == 0
        assert "stop_event" not in unloads[0][2]


def test_batch_requires_unload_confirmation(monkeypatch):
    client = ollama.OllamaClient(
        "http://localhost:11434", 2048, threading.Event(), release_on_close=True
    )
    client.owned_model = "small"
    monkeypatch.setattr(
        ollama,
        "request",
        lambda *_, **__: response({"done": True, "done_reason": "stop"}),
    )
    with pytest.raises(ValueError, match="confirm model unloading"):
        client.close()


def test_ollama_pipeline_receipts_cache_and_no_lm_lifecycle(tmp_path, monkeypatch):
    corpus = tmp_path / "quotes.json"
    corpus.write_text(
        json.dumps([{"_id": "one", "content": "Begin.", "author": "Fixture"}])
    )
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:0.6b")
    monkeypatch.setenv("QUOTES_FILE_PATH", str(corpus))
    monkeypatch.setenv("LM_STUDIO_CONTEXT_LENGTH", "invalid-unused-setting")
    monkeypatch.setattr(
        get_prompt,
        "ensure_lm_studio_model",
        Mock(side_effect=AssertionError("LM Studio touched")),
    )
    monkeypatch.setattr(
        get_prompt,
        "_unload_lm_studio_model",
        Mock(side_effect=AssertionError("LM Studio touched")),
    )
    replies = iter(
        [
            {"capabilities": ["completion"]},
            {"done": True, "message": {"content": '{"status":"ok"}'}},
            {"done": True, "message": {"content": '{"prompt":"sunlit path"}'}},
            {"done": True, "message": {"content": '{"hashtags":["#begin","#AIart"]}'}},
        ]
    )
    request = Mock(side_effect=lambda *_, **__: response(next(replies)))
    monkeypatch.setattr(ollama, "request", request)
    adapter = LivePrompt()
    metadata = adapter.metadata()
    first = run_workflow(
        corpus, stage="prompt", prompt=adapter, prompt_metadata=metadata
    )
    adapter.close()
    assert first["exit_code"] == 0 and first["items"][0]["prompt"] == "completed"
    state = json.loads((tmp_path / "quotes.json.state.json").read_text())
    assert state["items"]["one"]["prompt"]["generation"]["provider"] == "ollama"
    fresh = LivePrompt()
    second = run_workflow(
        corpus, stage="prompt", prompt=fresh, prompt_metadata=fresh.metadata()
    )
    fresh.close()
    assert second["items"][0]["prompt"] == "skipped" and request.call_count == 4
    assert fresh.runtime is None
    monkeypatch.setenv("LLM_PROVIDER", "lm_studio")
    monkeypatch.setenv("LM_STUDIO_CONTEXT_LENGTH", "2048")
    assert LivePrompt().metadata() != metadata


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_PROVIDER", "unknown"),
        ("OLLAMA_CONTEXT_LENGTH", "0"),
        ("OLLAMA_BASE_URL", ""),
        ("OLLAMA_MODEL", ""),
    ],
)
def test_invalid_settings(name, value, tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTES_FILE_PATH", str(tmp_path / "quotes.json"))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError):
        get_prompt._load_prompt_settings()
