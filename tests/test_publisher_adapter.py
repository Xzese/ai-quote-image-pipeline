from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quote_image_generator import publisher


def test_readiness_waits_for_finished(monkeypatch):
    responses = iter([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
    get = Mock(
        side_effect=lambda *_, **__: SimpleNamespace(json=lambda: next(responses))
    )
    remote = SimpleNamespace(
        _graph_base=lambda: "https://graph.fixture/v1.0", _token=lambda: "secret"
    )
    monkeypatch.setattr(publisher.time, "sleep", lambda _: None)
    publisher.wait_until_ready("container", publisher=remote, get=get)
    assert get.call_count == 2
    assert get.call_args.kwargs["params"] == {"fields": "status_code"}
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED", "PUBLISHED", None])
def test_unready_or_already_published_container_is_never_republished(status):
    remote = SimpleNamespace(
        _graph_base=lambda: "https://graph.fixture",
        _token=lambda: "secret",
        PublishingError=RuntimeError,
    )
    with pytest.raises(RuntimeError, match="not eligible"):
        publisher.wait_until_ready(
            "container",
            publisher=remote,
            get=lambda *_, **__: SimpleNamespace(json=lambda: {"status_code": status}),
        )


def test_production_adapter_checkpoints_before_publish(monkeypatch):
    from upload_photo import upload_photo as remote

    events = []
    monkeypatch.setattr(
        publisher, "configure_facebook_token_from_provider", lambda: None
    )
    monkeypatch.setattr(remote, "_graph_base", lambda: "fixture")
    monkeypatch.setattr(remote, "_account", lambda: "123")
    monkeypatch.setattr(remote, "_token", lambda: "secret")
    monkeypatch.setattr(remote, "upload_image", lambda _: "signed-private-url")
    monkeypatch.setattr(remote, "create_media_container", lambda *_: "container")
    monkeypatch.setattr(
        publisher, "wait_until_ready", lambda *_, **__: events.append("ready")
    )
    monkeypatch.setattr(
        remote,
        "publish_media_container",
        lambda _: events.append("publish") or {"id": "media"},
    )
    result = publisher.publish(
        "image.jpeg", "#fixture", checkpoint=lambda _: events.append("checkpoint")
    )
    assert events == ["checkpoint", "ready", "publish"] and result.media_id == "media"
