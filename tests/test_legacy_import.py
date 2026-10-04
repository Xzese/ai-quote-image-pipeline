"""Legacy assets enter the real publishing workflow without replay or regeneration."""

from copy import deepcopy
import json
import threading
from unittest.mock import Mock

from PIL import Image
import pytest

from quote_image_generator import legacy_import, pipeline, publisher, upload_quote_photo
from quote_image_generator.run_state import StateStore, atomic_json, digest, file_digest


def record(quote_id):
    return {
        "_id": quote_id,
        "content": "An existing quote.",
        "author": "Fixture",
        "prompt": "a sunlit meadow",
        "hashtags": "{#legacy #quote}",
    }


def image(directory, quote_id, *, size=(1024, 1024)):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{quote_id}1024x1024.jpeg"
    with Image.new("RGB", size, "blue") as fixture:
        fixture.save(path)
    return path


def configure(monkeypatch, corpus, directory):
    monkeypatch.setenv("QUOTES_FILE_PATH", str(corpus))
    monkeypatch.setenv("OUTPUT_IMAGE_PATH", str(directory.parent / "base"))
    monkeypatch.setenv("OVERLAY_OUTPUT_PATH", str(directory))
    monkeypatch.setenv("IG_BUSINESS_USER_ID", "123")
    monkeypatch.setenv("LEGACY_IMAGE_AUTO_IMPORT", "true")
    monkeypatch.setattr(pipeline, "load_project_env", lambda: None)


def test_upload_cli_imports_once_preserves_history_and_blocks_later_edits(
    tmp_path, monkeypatch, capsys
):
    corpus, directory = tmp_path / "quotes.json", tmp_path / "final"
    records = [record(name) for name in ("posted", "new", "altered")]
    atomic_json(corpus, records)
    paths = [image(directory, r["_id"]) for r in records]
    hashes = [file_digest(path) for path in paths]
    store = StateStore(corpus)
    source = store.item(records[0])["source"]
    key = digest({"account": "123", "quote_id": "posted", "source": source})
    prior = {
        "status": "published",
        "account": "123",
        "quote_id": "posted",
        "media_id": "previous-post",
    }
    store.data["publications"][key] = dict(prior)
    store.save()
    configure(monkeypatch, corpus, directory)
    posts = []
    monkeypatch.setattr(publisher, "prepare", lambda: None)
    monkeypatch.setattr(
        publisher,
        "publish",
        lambda path, caption: posts.append((path, caption)) or {"id": "new-post"},
    )
    monkeypatch.setattr(
        publisher, "alert", Mock(side_effect=AssertionError("No alert expected"))
    )
    monkeypatch.setattr(
        pipeline.LivePrompt,
        "metadata",
        Mock(side_effect=AssertionError("No generation expected")),
    )

    assert upload_quote_photo.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["legacy_import"]["imported"] == 3
    assert result["publish"]["quote_id"] == "new"
    assert posts == [(str(paths[1]), records[1]["hashtags"])]
    assert [file_digest(path) for path in paths] == hashes
    state = StateStore(corpus).data
    assert state["publications"][key] == prior
    assert (
        state["items"]["new"]["render"]["metadata"]["original_generation_metadata"]
        == "unknown"
    )
    assert state["items"]["new"]["render"]["path"] == str(
        directory / "new1024x1024.jpeg"
    )

    # Caption/source edits and file replacement cannot silently refresh old receipts.
    records[0]["content"] = "A changed source."
    records[1]["hashtags"] = "#changed"
    atomic_json(corpus, records)
    image(directory, "altered", size=(512, 512))
    assert upload_quote_photo.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["legacy_import"]["status"] == "already_completed"
    assert result["publish"]["status"] == "skipped" and len(posts) == 1
    assert StateStore(corpus).data["publications"][key] == prior


def test_preview_validates_assets_and_import_preserves_existing_receipts(
    tmp_path, monkeypatch, capsys
):
    corpus, directory = tmp_path / "quotes.json", tmp_path / "final"
    names = ("good", "tracked", "missing", "caption", "corrupt", "small", "escape")
    records = [record(name) for name in names]
    records[3]["prompt"] = ""
    atomic_json(corpus, records)
    image(directory, "good")
    image(directory, "tracked")
    image(directory, "caption")
    (directory / "corrupt1024x1024.jpeg").write_bytes(b"not a jpeg")
    image(directory, "small", size=(32, 32))
    outside = image(tmp_path / "outside", "escape")
    (directory / "escape1024x1024.jpeg").symlink_to(outside)
    store = StateStore(corpus)
    tracked = store.item(records[1])
    tracked["prompt"] = {"status": "failed", "input": "old-input"}
    store.data["publications"]["unknown-operation"] = {
        "status": "unknown",
        "account": "123",
    }
    store.save()
    original = store.path.read_bytes()
    original_corpus = corpus.read_bytes()
    monkeypatch.setattr(legacy_import, "load_project_env", lambda: None)

    argv = ["--corpus", str(corpus), "--image-dir", str(directory)]
    assert legacy_import.main([*argv, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["imported"] == 1 and preview["excluded"] == 6
    assert store.path.read_bytes() == original
    assert legacy_import.main(argv) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] == 1
    state = StateStore(corpus).data
    assert state["items"]["tracked"] == tracked
    assert state["publications"] == store.data["publications"]
    assert set(state["items"]) == {"good", "tracked"}
    assert corpus.read_bytes() == original_corpus


def test_auto_import_can_be_disabled_and_unknown_publication_still_blocks(
    tmp_path, monkeypatch, capsys
):
    corpus, directory = tmp_path / "quotes.json", tmp_path / "final"
    atomic_json(corpus, [record("one")])
    image(directory, "one")
    configure(monkeypatch, corpus, directory)
    prepare, post = Mock(), Mock(return_value={"id": "must-not-post"})
    monkeypatch.setattr(publisher, "prepare", prepare)
    monkeypatch.setattr(publisher, "publish", post)
    monkeypatch.setenv("LEGACY_IMAGE_AUTO_IMPORT", "false")
    assert upload_quote_photo.main() == 0
    assert json.loads(capsys.readouterr().out)["publish"]["status"] == "skipped"
    assert not StateStore(corpus).path.exists()
    store = StateStore(corpus)
    store.data["publications"]["unknown"] = {
        "status": "unknown",
        "account": "123",
        "quote_id": "one",
    }
    store.save()
    monkeypatch.setenv("LEGACY_IMAGE_AUTO_IMPORT", "true")
    assert upload_quote_photo.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["legacy_import"]["imported"] == 1
    assert result["publish"]["status"] == "unknown"
    prepare.assert_not_called()
    post.assert_not_called()


def test_source_edits_before_or_after_empty_migration_do_not_adopt_old_image(
    tmp_path, monkeypatch, capsys
):
    corpus, directory = tmp_path / "quotes.json", tmp_path / "final"
    records = [record("one")]
    atomic_json(corpus, records)
    image(directory, "one")
    store = StateStore(corpus)
    store.item(records[0])["prompt"] = {"status": "completed", "output": "old"}
    records[0]["content"] = "A different quote."
    store.item(records[0])
    store.save()
    result = legacy_import.import_legacy_images(records, directory, store)
    assert result["imported"] == 0 and "render" not in store.data["items"]["one"]

    # A successful zero-import pass must also freeze the migration boundary.
    empty_corpus = tmp_path / "initially-incomplete.json"
    incomplete = [record("one")]
    incomplete[0]["hashtags"] = ""
    atomic_json(empty_corpus, incomplete)
    configure(monkeypatch, empty_corpus, directory)
    post = Mock(return_value={"id": "must-not-post"})
    monkeypatch.setattr(publisher, "publish", post)
    assert upload_quote_photo.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert first["legacy_import"]["imported"] == 0
    assert StateStore(empty_corpus).data["legacy_import"]["version"] == 1
    incomplete[0]["content"] = "A different quote for the same filename."
    incomplete[0]["hashtags"] = "#now-present"
    atomic_json(empty_corpus, incomplete)
    assert upload_quote_photo.main() == 0
    second = json.loads(capsys.readouterr().out)
    assert second["legacy_import"]["status"] == "already_completed"
    assert second["publish"]["status"] == "skipped"
    post.assert_not_called()


def test_cancelled_or_failed_migration_leaves_existing_state_unchanged(
    tmp_path, monkeypatch
):
    corpus, directory = tmp_path / "quotes.json", tmp_path / "final"
    records = [record("one")]
    image(directory, "one")
    store = StateStore(corpus)
    store.item(records[0])
    store.save()
    before = deepcopy(store.data)
    original = store.path.read_bytes()
    cancelled = threading.Event()
    cancelled.set()
    result = legacy_import.import_legacy_images(
        records, directory, store, stop_event=cancelled
    )
    assert result["status"] == "cancelled" and store.data == before
    with monkeypatch.context() as failure:
        failure.setattr(store, "save", Mock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            legacy_import.import_legacy_images(records, directory, store)
    assert store.data == before and store.path.read_bytes() == original
    assert (
        legacy_import.import_legacy_images(records, directory, store)["imported"] == 1
    )
