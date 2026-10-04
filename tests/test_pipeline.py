"""Workflow contracts: restart safety, stale artefacts and partial progress."""

import json
import multiprocessing
from pathlib import Path
import threading
from unittest.mock import Mock

from PIL import Image
import pytest

from quote_image_generator.pipeline import run_workflow, LiveRender
from quote_image_generator.run_state import (
    StateStore,
    atomic_json,
    corpus_lock,
    file_digest,
)
from quote_image_generator.reconcile import reconcile
from quote_image_generator.offline import demo


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "corpus.json"
    atomic_json(
        path,
        [
            {"_id": "one", "content": "A small step.", "author": "Fixture"},
            {"_id": "two", "content": "A new day.", "author": "Fixture"},
        ],
    )
    return path


def prompt(record):
    return {"prompt": "sunlit meadow at dawn", "hashtags": "#nature #AIart"}


def render_at(directory):
    def render(record):
        path = directory / (record["_id"] + ".jpeg")
        with Image.new("RGB", (32, 32), "blue") as image:
            image.save(path)
        return {"path": str(path), "seed": 42}

    return render


def run(corpus, **kwargs):
    return run_workflow(
        corpus,
        prompt=kwargs.pop("prompt", prompt),
        render=kwargs.pop("render", render_at(corpus.parent)),
        prompt_metadata=kwargs.pop("prompt_metadata", {"model": "fixture"}),
        render_metadata=kwargs.pop("render_metadata", {"seed": 42}),
        **kwargs,
    )


def test_restart_skips_completed_stages_without_network(corpus):
    assert run(corpus)["counts"]["completed"] == 4
    p, r = Mock(side_effect=AssertionError()), Mock(side_effect=AssertionError())
    result = run(corpus, prompt=p, render=r)
    assert result["exit_code"] == 0 and result["counts"]["skipped"] == 5
    p.assert_not_called()
    r.assert_not_called()


def test_partial_failure_retains_success_and_exits_nonzero(corpus):
    def sometimes(record):
        if record["_id"] == "two":
            raise RuntimeError("private provider response")
        return prompt(record)

    result = run(corpus, prompt=sometimes)
    assert result["status"] == "partial" and result["exit_code"] == 1
    assert result["items"] == [
        {"quote_id": "one", "prompt": "completed", "render": "completed"},
        {"quote_id": "two", "prompt": "failed", "render": "blocked"},
    ]
    assert result["eligible_renders"] == 1
    assert "private" not in json.dumps(result)
    result = run(corpus)
    assert result["items"][0]["prompt"] == "skipped"
    assert result["items"][1]["render"] == "completed"


def test_all_failures_are_failed_not_partial(corpus):
    result = run(corpus, prompt=lambda _: None)
    assert result["status"] == "failed" and result["exit_code"] == 1


@pytest.mark.parametrize(
    "generated",
    [
        {"prompt": " ".join(["long"] * 51), "hashtags": "#ok"},
        {"prompt": "two\nlines", "hashtags": "#ok"},
        {"prompt": "sky", "hashtags": "no-hash"},
        {"prompt": "sky", "hashtags": ""},
    ],
)
def test_invalid_generation_never_receives_completed_receipt(corpus, generated):
    result = run(corpus, prompt=lambda _: generated)
    assert result["exit_code"] == 1 and result["eligible_renders"] == 0


def test_changed_model_or_seed_invalidates_receipts(corpus):
    run(corpus)
    p, r = Mock(side_effect=prompt), Mock(side_effect=render_at(corpus.parent))
    run(corpus, prompt=p, render=r, prompt_metadata={"model": "new-model"})
    assert p.call_count == 2 and r.call_count == 2
    p.reset_mock()
    r.reset_mock()
    run(
        corpus,
        prompt=p,
        render=r,
        prompt_metadata={"model": "new-model"},
        render_metadata={"seed": 99},
    )
    p.assert_not_called()
    assert r.call_count == 2


def test_tampered_artefact_is_rerendered(corpus):
    run(corpus)
    (corpus.parent / "one.jpeg").write_bytes(b"corrupt")
    r = Mock(side_effect=render_at(corpus.parent))
    result = run(corpus, render=r)
    assert result["exit_code"] == 0 and r.call_count == 1
    assert r.call_args.args[0]["_id"] == "one"


def test_render_only_cannot_adopt_existing_legacy_images(corpus):
    for record in json.loads(corpus.read_text()):
        render_at(corpus.parent)(record)
    r = Mock()
    result = run(corpus, stage="render", render=r)
    assert result["exit_code"] == 1 and result["counts"]["blocked"] == 2
    r.assert_not_called()


def test_invalid_image_never_becomes_publishable(corpus):
    def bad_render(record):
        path = corpus.parent / (record["_id"] + ".jpeg")
        path.write_bytes(b"not an image")
        return {"path": str(path)}

    post = Mock()
    result = run(corpus, render=bad_render, publish=post)
    assert result["eligible_renders"] == 0 and result["exit_code"] == 1
    post.assert_not_called()


def test_manual_prompt_or_source_edit_blocks_publication(corpus):
    run(corpus)
    records = json.loads(corpus.read_text())
    records[0]["prompt"] = "manually changed"
    records[1]["content"] = "different quote"
    atomic_json(corpus, records)
    post = Mock()
    result = run(corpus, stage="publish", publish=post)
    assert result["publish"]["status"] == "skipped"
    post.assert_not_called()


def test_one_confirmed_publication_per_source_and_account(corpus, monkeypatch):
    monkeypatch.setenv("IG_BUSINESS_USER_ID", "123")
    post = Mock(return_value={"id": "media"})
    run(corpus, publish=post)
    run(corpus, stage="publish", publish=post)
    result = run(corpus, stage="publish", publish=post)
    assert post.call_count == 2 and result["publish"]["status"] == "skipped"
    # Re-rendering a published source does not remove its publication ownership.
    run(corpus, render_metadata={"seed": 123}, publish=post)
    assert post.call_count == 2


def test_uncertain_publication_blocks_account_across_restart_and_source_change(
    corpus, monkeypatch
):
    monkeypatch.setenv("IG_BUSINESS_USER_ID", "123")
    post = Mock(side_effect=TimeoutError("secret"))
    first = run(corpus, publish=post)
    assert first["publish"]["status"] == "unknown"
    records = json.loads(corpus.read_text())
    records[0]["content"] = "new source"
    atomic_json(corpus, records)
    second = run(corpus, publish=post)
    assert second["publish"]["operation_id"] == first["publish"]["operation_id"]
    post.assert_called_once()


def test_container_checkpoint_is_durable_before_remote_publish(corpus):
    def post(path, caption, *, checkpoint):
        checkpoint("container")
        data = StateStore(corpus).data["publications"]
        operation = next(iter(data.values()))
        assert (
            operation["status"] == "unknown" and operation["creation_id"] == "container"
        )
        raise TimeoutError()

    post.supports_checkpoint = True
    result = run(corpus, publish=post)
    assert result["publish"]["creation_id"] == "container"


def test_crash_during_publish_leaves_claim_and_never_replays(corpus):
    def crash(*_):
        raise KeyboardInterrupt()

    assert run(corpus, publish=crash)["exit_code"] == 130
    post = Mock()
    assert run(corpus, stage="publish", publish=post)["publish"]["status"] == "unknown"
    post.assert_not_called()


def test_publication_journal_failure_prevents_external_effect(corpus, monkeypatch):
    run(corpus)
    monkeypatch.setattr(StateStore, "save", lambda _: (_ for _ in ()).throw(OSError()))
    post = Mock()
    assert run(corpus, stage="publish", publish=post)["exit_code"] == 1
    post.assert_not_called()


def test_operator_confirmation_prevents_replay(corpus):
    first = run(corpus, publish=Mock(side_effect=TimeoutError()))
    key = first["publish"]["operation_id"]
    reconcile(corpus, key, media_id="confirmed")
    post = Mock(return_value={"id": "next"})
    run(corpus, stage="publish", publish=post)
    assert post.call_count == 1 and Path(post.call_args.args[0]).name == "two.jpeg"


def test_operator_nonpublication_enables_new_attempt_and_retains_history(corpus):
    first = run(corpus, publish=Mock(side_effect=TimeoutError()))
    key = first["publish"]["operation_id"]
    reconcile(corpus, key, not_published=True)
    post = Mock(return_value={"id": "confirmed"})
    run(corpus, stage="publish", publish=post)
    post.assert_called_once()
    journal = StateStore(corpus).data["publications"][key]
    assert journal["status"] == "published"
    assert journal["prior_attempts"][0]["status"] == "not_published"


def _hold_lock(corpus, ready, release):
    with corpus_lock(corpus):
        ready.set()
        release.wait(10)


def test_overlapping_process_exits_busy_without_effects(corpus):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(str(corpus), ready, release))
    process.start()
    try:
        assert ready.wait(5)
        callback = Mock()
        assert run(corpus, prompt=callback)["exit_code"] == 75
        callback.assert_not_called()
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()


def test_shared_image_directory_lock_blocks_different_corpora(corpus):
    directory = corpus.parent / "shared"
    with corpus_lock(directory / ".pipeline"):
        result = run(corpus, resource_dirs=[directory])
    assert result["exit_code"] == 75


def test_cancelled_render_keeps_completed_prompts_and_returns_130(corpus):
    def cancel(_):
        raise InterruptedError()

    result = run(corpus, render=cancel)
    assert result["exit_code"] == 130 and result["status"] == "cancelled"
    assert StateStore(corpus).data["items"]["one"]["prompt"]["status"] == "completed"


def test_offline_demo_is_network_free_and_reproducible(tmp_path, monkeypatch):
    import requests

    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("network")),
    )
    first, second = tmp_path / "a", tmp_path / "b"
    assert demo(first)["eligible_renders"] == 6
    assert demo(second)["eligible_renders"] == 6
    assert {p.name: file_digest(p) for p in first.glob("*.jpeg")} == {
        p.name: file_digest(p) for p in second.glob("*.jpeg")
    }
    assert demo(first)["counts"]["skipped"] == 13


def test_live_render_records_actual_workflow_and_seed(tmp_path, monkeypatch):
    from quote_image_generator import get_image as gi
    from quote_image_generator import deadline

    monkeypatch.setenv("OUTPUT_IMAGE_PATH", str(tmp_path / "base"))
    monkeypatch.setenv("OVERLAY_OUTPUT_PATH", str(tmp_path / "final"))
    monkeypatch.setenv("GENERATION_SEED", "123")
    captured = {}

    def queue(session, url, workflow, post_request=None):
        captured.update(workflow=workflow)
        return "owned-prompt"

    monkeypatch.setattr(gi, "queue_prompt", queue)

    def wait(*args, **kwargs):
        captured["deadline"] = kwargs["deadline"]
        return {"filename": "image.png"}

    monkeypatch.setattr(gi, "wait_for_image", wait)

    def download(session, url, info, path, get_request=None):
        with Image.new("RGB", (1024, 1024), "blue") as image:
            image.save(path)

    monkeypatch.setattr(gi, "download_image", download)
    monkeypatch.setattr(
        deadline, "request", Mock(side_effect=AssertionError("unexpected network"))
    )
    adapter = LiveRender(threading.Event())
    result = adapter(
        {
            "_id": "one",
            "prompt": "sunlit meadow",
            "content": "A step.",
            "author": "Fixture",
        }
    )
    assert (
        result["seed"]
        == captured["workflow"][gi.SAMPLER_NODE_ID]["inputs"]["seed"]
        == 123
    )
    assert result["prompt_id"] == "owned-prompt" and result[
        "generation_prompt"
    ].startswith("sunlit meadow")
    assert (
        adapter.metadata()["models"]["57:28"]["unet_name"]
        == "z_image_turbo_bf16.safetensors"
    )
    assert Path(result["path"]).is_file()


def test_confirmation_persistence_failure_remains_unknown(corpus, monkeypatch):
    run(corpus)
    original_save = StateStore.save

    def fail_after_confirmation(store):
        if any(
            operation["status"] == "published"
            for operation in store.data["publications"].values()
        ):
            raise OSError("disk full")
        original_save(store)

    monkeypatch.setattr(StateStore, "save", fail_after_confirmation)
    post = Mock(return_value={"id": "confirmed"})
    result = run(corpus, stage="publish", publish=post)
    assert result["publish"]["status"] == "unknown"
    run(corpus, stage="publish", publish=post)
    post.assert_called_once()


def test_preparation_failure_never_claims_or_publishes(corpus):
    run(corpus)
    post = Mock()
    result = run(
        corpus,
        stage="publish",
        publish=post,
        prepare=Mock(side_effect=ValueError("missing config")),
    )
    assert result["publish"]["status"] == "failed"
    assert StateStore(corpus).data["publications"] == {}
    post.assert_not_called()


def test_notification_failure_never_repeats_uncertain_publication(corpus):
    post = Mock(side_effect=TimeoutError())
    result = run(corpus, publish=post, alert=Mock(side_effect=OSError()))
    assert result["publish"]["status"] == "unknown"
    post.assert_called_once()


def test_publish_selection_honours_configured_output_directory(corpus):
    run(corpus)
    post = Mock()
    result = run(
        corpus, stage="publish", publish=post, publication_dir=corpus.parent / "other"
    )
    assert result["publish"]["status"] == "skipped"
    post.assert_not_called()


def test_corrupt_state_fails_closed(corpus):
    run(corpus)
    Path(str(corpus) + ".state.json").write_text("invalid-json")
    post = Mock()
    assert run(corpus, stage="publish", publish=post)["exit_code"] == 1
    post.assert_not_called()


def test_queue_poll_and_download_share_one_deadline(tmp_path, monkeypatch):
    from quote_image_generator import deadline, get_image as gi
    import requests

    monkeypatch.setenv("OUTPUT_IMAGE_PATH", str(tmp_path / "base"))
    monkeypatch.setenv("OVERLAY_OUTPUT_PATH", str(tmp_path / "final"))
    monkeypatch.setenv("COMFYUI_DEADLINE_SECONDS", "1")
    tick = [0.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: tick[0])
    calls = []
    deadlines = []

    def slow_request(method, url, *, deadline, **kwargs):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/queue"):
            return None
        deadlines.append(deadline)
        tick[0] += 0.4
        deadline.remaining()
        response = requests.Response()
        response.status_code = 200
        if url.endswith("/prompt"):
            response._content = b'{"prompt_id":"owned"}'
        else:
            response._content = (
                b'{"owned":{"outputs":{"save":{"images":[{"filename":"render.png"}]}}}}'
            )
        return response

    monkeypatch.setattr(deadline, "request", slow_request)
    adapter = LiveRender(threading.Event())
    with pytest.raises(gi.ComfyUIError):
        adapter({"_id": "one", "prompt": "sky", "content": "Quote", "author": "Author"})
    assert calls == ["prompt", "owned", "view", "queue"]
    assert len({id(value) for value in deadlines}) == 1
    assert not (tmp_path / "final" / "one1024x1024.jpeg").exists()
