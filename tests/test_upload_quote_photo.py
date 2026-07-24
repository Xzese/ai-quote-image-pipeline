from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


class _FakeRandom:
    def __init__(self, value):
        self.value = value

    def choice(self, _sequence):
        return self.value


class _FailingPost:
    def __init__(self):
        self.calls = 0

    def __call__(self, _path, _caption):
        self.calls += 1
        raise RuntimeError("forced post failure")


def _import_upload_quote_photo(
    monkeypatch: pytest.MonkeyPatch,
    *,
    upload_image_fn=None,
    create_media_container_fn=None,
    publish_media_container_fn=None,
    send_email_alert_fn=None,
):
    fake_upload_photo_pkg = types.ModuleType("upload_photo")
    fake_upload_photo = types.ModuleType("upload_photo.upload_photo")
    fake_upload_photo.upload_image = upload_image_fn or (lambda *_args, **_kwargs: None)
    fake_upload_photo.create_media_container = create_media_container_fn or (
        lambda *_args, **_kwargs: None
    )
    fake_upload_photo.publish_media_container = publish_media_container_fn or (
        lambda *_args, **_kwargs: None
    )
    fake_upload_photo.send_email_alert = send_email_alert_fn or (
        lambda *_args, **_kwargs: None
    )

    monkeypatch.setitem(sys.modules, "upload_photo", fake_upload_photo_pkg)
    monkeypatch.setitem(sys.modules, "upload_photo.upload_photo", fake_upload_photo)

    module_name = "quote_image_generator.upload_quote_photo"
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _write_quotes(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"_id": "q001", "content": "quote", "author": "Author", "hashtags": "#quote"},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _set_upload_env(
    monkeypatch, quotes_file, image_dir, max_attempts=3, retry_base="2.0"
):
    monkeypatch.setenv("QUOTES_FILE_PATH", str(quotes_file))
    monkeypatch.setenv("OVERLAY_OUTPUT_PATH", str(image_dir))
    monkeypatch.setenv("UPLOAD_QUOTE_MAX_ATTEMPTS", str(max_attempts))
    monkeypatch.setenv("UPLOAD_QUOTE_RETRY_BASE_SECONDS", str(retry_base))


def test_main_posts_with_retry_backoff_and_returns_failure_after_max_attempts(
    monkeypatch, tmp_path
):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(
        monkeypatch, quotes_file, output_dir, max_attempts=4, retry_base="2"
    )

    fake_post = _FailingPost()
    sleeps: list[float] = []
    alerts: list[tuple[str, str]] = []
    upload_quote_photo = _import_upload_quote_photo(monkeypatch)

    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)
    result = upload_quote_photo.main(
        post_func=fake_post,
        alert_func=lambda subject, body: alerts.append((subject, body)),
        sleep_func=lambda delay: sleeps.append(delay),
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert result == 1
    assert fake_post.calls == 4
    assert sleeps == [2, 4, 8]
    assert len(alerts) == 1
    assert alerts[0][0] == "[Instagram AI Image] Posting Failed"
    assert "after 4 attempts: forced post failure" in alerts[0][1]
    assert "q0011024x1024.jpeg" in alerts[0][1]


def test_main_returns_zero_immediately_after_success(monkeypatch, tmp_path):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(
        monkeypatch, quotes_file, output_dir, max_attempts=3, retry_base="3"
    )

    posts: list[tuple[str, str]] = []
    alerts: list[tuple[str, str]] = []

    def post_once(_path, caption):
        posts.append((_path, caption))
        return None

    sleeps: list[float] = []
    upload_quote_photo = _import_upload_quote_photo(monkeypatch)
    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)
    result = upload_quote_photo.main(
        post_func=post_once,
        alert_func=lambda subject, body: alerts.append((subject, body)),
        sleep_func=lambda delay: sleeps.append(delay),
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert result == 0
    assert len(posts) == 1
    assert sleeps == []
    assert alerts == []
    assert posts[0][1] == "#quote"


def test_main_calls_facebook_token_provider_once_before_success(monkeypatch, tmp_path):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(monkeypatch, quotes_file, output_dir)
    upload_quote_photo = _import_upload_quote_photo(monkeypatch)
    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)

    events: list[str] = []

    def provider_success():
        events.append("provider")
        return True

    monkeypatch.setattr(
        upload_quote_photo,
        "configure_facebook_token_from_provider",
        provider_success,
    )

    def post_once(_path, caption):
        events.append("post")
        return None

    result = upload_quote_photo.main(
        post_func=post_once,
        alert_func=lambda *_args, **_kwargs: None,
        sleep_func=lambda *_args, **_kwargs: None,
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert result == 0
    assert events == ["provider", "post"]


def test_main_returns_failure_when_provider_raises_without_post(monkeypatch, tmp_path):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(monkeypatch, quotes_file, output_dir)
    upload_quote_photo = _import_upload_quote_photo(monkeypatch)
    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)

    def provider_raises():
        raise upload_quote_photo.FacebookTokenProviderError("token provider failed")

    monkeypatch.setattr(
        upload_quote_photo,
        "configure_facebook_token_from_provider",
        provider_raises,
    )

    called = {"post": 0}

    result = upload_quote_photo.main(
        post_func=lambda *_args, **_kwargs: called.__setitem__(
            "post", called["post"] + 1
        ),
        alert_func=lambda *_args, **_kwargs: None,
        sleep_func=lambda *_args, **_kwargs: None,
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert result == 1
    assert called["post"] == 0


def test_main_returns_failure_when_email_alert_itself_fails(monkeypatch, tmp_path):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(
        monkeypatch, quotes_file, output_dir, max_attempts=1, retry_base="2"
    )

    upload_quote_photo = _import_upload_quote_photo(monkeypatch)
    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)

    def failing_alert(_subject, _body):
        raise RuntimeError("SMTP unavailable")

    result = upload_quote_photo.main(
        post_func=_FailingPost(),
        alert_func=failing_alert,
        sleep_func=lambda _delay: None,
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert result == 1


def test_main_does_not_call_default_post_function_when_injected(monkeypatch, tmp_path):
    quotes_file = tmp_path / "quotes.json"
    output_dir = tmp_path / "output"
    _write_quotes(quotes_file)
    _set_upload_env(monkeypatch, quotes_file, output_dir)
    upload_quote_photo = _import_upload_quote_photo(monkeypatch)

    monkeypatch.setattr(upload_quote_photo, "load_project_env", lambda: None)

    default_called = {"value": False}

    def default_post(*_args, **_kwargs):
        default_called["value"] = True
        raise AssertionError("default post should not be called")

    monkeypatch.setattr(upload_quote_photo, "_post_quote_photo", default_post)

    upload_quote_photo.main(
        post_func=lambda *_args, **_kwargs: None,
        sleep_func=lambda *_args, **_kwargs: None,
        randomizer=_FakeRandom(
            {
                "_id": "q001",
                "content": "quote",
                "author": "Author",
                "hashtags": "#quote",
            }
        ),
    )

    assert default_called["value"] is False


def test_post_quote_photo_raises_when_no_token_is_returned(monkeypatch, tmp_path):
    fake_image_path = tmp_path / "image.jpeg"
    fake_image_path.write_text("image", encoding="utf-8")

    upload_quote_photo = _import_upload_quote_photo(
        monkeypatch,
        upload_image_fn=lambda *_args, **_kwargs: "https://example.test/image.jpeg",
        create_media_container_fn=lambda *_args, **_kwargs: "No Valid Token",
        publish_media_container_fn=lambda *_args, **_kwargs: {"id": "published"},
    )

    with pytest.raises(RuntimeError, match="No Valid Token"):
        upload_quote_photo._post_quote_photo(str(fake_image_path), "#quote")


def test_post_quote_photo_raises_when_publish_response_is_falsey(monkeypatch, tmp_path):
    fake_image_path = tmp_path / "image.jpeg"
    fake_image_path.write_text("image", encoding="utf-8")

    upload_quote_photo = _import_upload_quote_photo(
        monkeypatch,
        upload_image_fn=lambda *_args, **_kwargs: "https://example.test/image.jpeg",
        create_media_container_fn=lambda *_args, **_kwargs: "container123",
        publish_media_container_fn=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="publish_media_container returned empty"):
        upload_quote_photo._post_quote_photo(str(fake_image_path), "#quote")


def test_post_quote_photo_returns_truthy_response(monkeypatch, tmp_path):
    fake_image_path = tmp_path / "image.jpeg"
    fake_image_path.write_text("image", encoding="utf-8")

    upload_quote_photo = _import_upload_quote_photo(
        monkeypatch,
        upload_image_fn=lambda *_args, **_kwargs: "https://example.test/image.jpeg",
        create_media_container_fn=lambda *_args, **_kwargs: "container123",
        publish_media_container_fn=lambda *_args, **_kwargs: {"id": "media-id"},
    )

    result = upload_quote_photo._post_quote_photo(str(fake_image_path), "#quote")

    assert result == {"id": "media-id"}
