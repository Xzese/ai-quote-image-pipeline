import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from quote_image_generator import get_quotes, pipeline
from quote_image_generator.run_state import atomic_json, corpus_lock, RunBusy


QUOTES = [
    {"_id": "one", "content": "Begin.", "author": "Fixture"},
    {"_id": "two", "content": "Keep going.", "author": "Fixture"},
]


def callbacks(tmp_path, events):
    def prompt(record):
        events.append("prompt:" + record["_id"])
        return {"prompt": "sunlit path", "hashtags": "#begin"}

    def render(record):
        events.append("render:" + record["_id"])
        path = tmp_path / (record["_id"] + ".jpeg")
        with Image.new("RGB", (32, 32), "blue") as image:
            image.save(path)
        return {"path": str(path)}

    return prompt, render


@pytest.mark.parametrize(
    "mode,expected",
    [
        (
            "batch",
            [
                "fetch",
                "prompt:one",
                "prompt:two",
                "release",
                "render:one",
                "render:two",
            ],
        ),
        (
            "per_quote",
            [
                "fetch",
                "prompt:one",
                "render:one",
                "prompt:two",
                "render:two",
                "release",
            ],
        ),
    ],
)
def test_order_fetches_under_lock_then_processes_requested_mode(
    tmp_path, mode, expected
):
    corpus = tmp_path / "quotes.json"
    events = []
    prompt, render = callbacks(tmp_path, events)

    def fetch():
        with pytest.raises(RunBusy):
            with corpus_lock(corpus):
                pass
        events.append("fetch")
        return QUOTES

    result = pipeline.run_workflow(
        corpus,
        mode=mode,
        fetch=fetch,
        prompt=prompt,
        render=render,
        after_prompt=lambda: events.append("release"),
    )
    assert result["exit_code"] == 0 and result["fetch"]["records"] == 2
    assert events == expected
    assert result["mode"] == mode


def test_default_batch_refresh_preserves_cache_across_mode_changes(tmp_path):
    corpus = tmp_path / "quotes.json"
    events = []
    prompt, render = callbacks(tmp_path, events)
    first = pipeline.run_workflow(
        corpus, fetch=lambda: QUOTES, prompt=prompt, render=render
    )
    assert first["mode"] == "batch" and first["exit_code"] == 0
    second = pipeline.run_workflow(
        corpus,
        mode="per_quote",
        fetch=lambda: QUOTES,
        prompt=Mock(side_effect=AssertionError()),
        render=Mock(side_effect=AssertionError()),
    )
    assert second["exit_code"] == 0 and second["eligible_renders"] == 2
    assert all(
        item["prompt"] == item["render"] == "skipped" for item in second["items"]
    )
    changed = [dict(q) for q in QUOTES]
    changed[1]["content"] = "A changed quote."
    events.clear()
    third = pipeline.run_workflow(
        corpus, fetch=lambda: changed, prompt=prompt, render=render
    )
    assert third["exit_code"] == 0 and events == ["prompt:two", "render:two"]


@pytest.mark.parametrize("fetched", [QUOTES[:1], []])
def test_absent_quotes_keep_completed_receipts_and_images(tmp_path, fetched):
    corpus = tmp_path / "quotes.json"
    prompt, render = callbacks(tmp_path, [])
    assert (
        pipeline.run_workflow(
            corpus, fetch=lambda: QUOTES, prompt=prompt, render=render
        )["exit_code"]
        == 0
    )
    previous = json.loads(corpus.read_text())
    state = Path(str(corpus) + ".state.json").read_bytes()
    images = {path.name: path.read_bytes() for path in tmp_path.glob("*.jpeg")}
    result = pipeline.run_workflow(
        corpus,
        fetch=lambda: fetched,
        prompt=Mock(side_effect=AssertionError()),
        render=Mock(side_effect=AssertionError()),
    )
    assert result["exit_code"] == 0 and result["fetch"]["records"] == 2
    assert all(
        item["prompt"] == item["render"] == "skipped" for item in result["items"]
    )
    assert json.loads(corpus.read_text()) == previous
    assert Path(str(corpus) + ".state.json").read_bytes() == state
    assert {path.name: path.read_bytes() for path in tmp_path.glob("*.jpeg")} == images


@pytest.mark.parametrize(
    "failure", [RuntimeError("private response"), ValueError("bad payload")]
)
def test_fetch_failure_preserves_corpus_and_prevents_generation(tmp_path, failure):
    corpus = tmp_path / "quotes.json"
    atomic_json(corpus, QUOTES)
    before = corpus.read_bytes()
    prompt = Mock()
    render = Mock()
    result = pipeline.run_workflow(
        corpus, fetch=Mock(side_effect=failure), prompt=prompt, render=render
    )
    assert result["exit_code"] == 1 and result["fetch"]["status"] == "failed"
    assert corpus.read_bytes() == before
    prompt.assert_not_called()
    render.assert_not_called()
    assert "private" not in json.dumps(result)
    assert json.loads(Path(str(corpus) + ".summary.json").read_text()) == result


def test_fetch_cancellation_preserves_corpus(tmp_path):
    corpus = tmp_path / "quotes.json"
    atomic_json(corpus, QUOTES)
    stop = threading.Event()
    before = corpus.read_bytes()

    def fetch():
        stop.set()
        return [dict(QUOTES[0], content="changed")]

    result = pipeline.run_workflow(
        corpus, fetch=fetch, stop_event=stop, prompt=Mock(), render=Mock()
    )
    assert result["exit_code"] == 130 and corpus.read_bytes() == before


def test_model_release_failure_blocks_render_and_publish(tmp_path):
    corpus = tmp_path / "quotes.json"
    atomic_json(corpus, QUOTES)
    prompt, _ = callbacks(tmp_path, [])
    render = Mock()
    publish = Mock()
    result = pipeline.run_workflow(
        corpus,
        prompt=prompt,
        render=render,
        publish=publish,
        after_prompt=Mock(side_effect=RuntimeError("cannot unload")),
    )
    assert result["exit_code"] == 1 and result["model_release"]["status"] == "failed"
    assert all(
        item["prompt"] == "completed" and item["render"] == "blocked"
        for item in result["items"]
    )
    render.assert_not_called()
    publish.assert_not_called()


def test_prompt_failure_keeps_batch_rendering_successful_quotes(tmp_path):
    corpus = tmp_path / "quotes.json"
    atomic_json(corpus, QUOTES)
    events = []
    prompt, render = callbacks(tmp_path, events)

    def sometimes(record):
        if record["_id"] == "two":
            raise ValueError("failed")
        return prompt(record)

    result = pipeline.run_workflow(
        corpus,
        prompt=sometimes,
        render=render,
        after_prompt=lambda: events.append("release"),
    )
    assert result["exit_code"] == 1 and result["eligible_renders"] == 1
    assert events == ["prompt:one", "release", "render:one"]
    assert result["items"][1]["render"] == "blocked"


@pytest.mark.parametrize(
    "mode_arg,env_mode,expected",
    [
        (None, None, "batch"),
        (None, "per_quote", "per_quote"),
        ("batch", "per_quote", "batch"),
    ],
)
def test_cli_default_and_overrides(
    tmp_path, monkeypatch, capsys, mode_arg, env_mode, expected
):
    events = []
    prompt, render = callbacks(tmp_path, events)
    corpus = tmp_path / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(corpus))
    monkeypatch.setenv("OUTPUT_IMAGE_PATH", str(tmp_path / "base"))
    monkeypatch.setenv("OVERLAY_OUTPUT_PATH", str(tmp_path / "final"))
    monkeypatch.setenv("PIPELINE_FETCH_QUOTES", "true")
    monkeypatch.delenv("PIPELINE_MODE", raising=False)
    if env_mode:
        monkeypatch.setenv("PIPELINE_MODE", env_mode)

    def fetch(**_):
        events.append("fetch")
        return QUOTES

    monkeypatch.setattr(get_quotes, "retrieve_quotes", fetch)

    class Prompt:
        def __init__(self, **kwargs):
            self.active = False

        def metadata(self):
            return {}

        def __call__(self, record):
            self.active = True
            return prompt(record)

        def close(self):
            if self.active:
                events.append("release")
                self.active = False

    class Render:
        def __init__(self, *_):
            pass

        def metadata(self):
            return {}

        def __call__(self, record):
            return render(record)

    monkeypatch.setattr(pipeline, "LivePrompt", Prompt)
    monkeypatch.setattr(pipeline, "LiveRender", Render)
    assert pipeline.main(["--mode", mode_arg] if mode_arg else []) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == expected
    if expected == "batch":
        assert events == [
            "fetch",
            "prompt:one",
            "prompt:two",
            "release",
            "render:one",
            "render:two",
        ]
    else:
        assert events == [
            "fetch",
            "prompt:one",
            "render:one",
            "prompt:two",
            "render:two",
            "release",
        ]


@pytest.mark.parametrize(
    "args,fetch_setting",
    [
        (["--skip-fetch"], "true"),
        ([], "false"),
        (["--stage", "prompt"], "true"),
        (["--stage", "render"], "true"),
        (["--stage", "publish"], "true"),
    ],
)
def test_cli_fetch_opt_out_and_stage_commands(
    tmp_path, monkeypatch, args, fetch_setting
):
    corpus = tmp_path / "quotes.json"
    atomic_json(corpus, QUOTES)
    monkeypatch.setenv("QUOTES_FILE_PATH", str(corpus))
    monkeypatch.setenv("PIPELINE_FETCH_QUOTES", fetch_setting)
    fetch = Mock(side_effect=AssertionError("unexpected fetch"))
    monkeypatch.setattr(get_quotes, "retrieve_quotes", fetch)
    run = Mock(return_value={"exit_code": 0})
    monkeypatch.setattr(pipeline, "run_workflow", run)
    assert pipeline.main(args) == 0
    assert run.call_args.kwargs["fetch"] is None
    fetch.assert_not_called()
