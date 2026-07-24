from __future__ import annotations

from PIL import Image, ImageDraw
import pytest

from quote_image_generator.get_image import (
    TEXT_NODE_ID,
    DIMENSIONS_NODE_ID,
    SAMPLER_NODE_ID,
    WorkflowValidationError,
    build_workflow,
    clear_owned_queue,
    cancel_comfyui_work,
    flatten_rgba_to_rgb,
    interrupt_owned_work,
    overlay_text_on_image,
    _fit_author_font_size,
    validate_workflow_shape,
    _fit_text_block,
    get_queue_snapshot,
    is_prompt_running,
    resolve_workflow_path,
)


class _Response:
    def __init__(self, ok: bool = True):
        self.ok = ok

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError("HTTP error")


class _PayloadResponse(_Response):
    def __init__(self, payload: object, ok: bool = True):
        super().__init__(ok=ok)
        self.payload = payload

    def json(self) -> object:
        return self.payload


def _base_workflow() -> dict:
    return {
        TEXT_NODE_ID: {"inputs": {"text": "old"}},
        DIMENSIONS_NODE_ID: {"inputs": {"width": 64, "height": 64}},
        SAMPLER_NODE_ID: {"inputs": {"steps": 10, "cfg": 1, "seed": 1}},
        "metadata": {"nested": {"values": [1, 2, 3]}},
    }


def test_build_workflow_deep_copies_base_workflow_and_preserves_base():
    base_workflow = _base_workflow()
    expected_prompt = "new quote prompt"

    result = build_workflow(
        base_workflow, expected_prompt, width=128, height=256, seed=123
    )

    assert result is not base_workflow
    assert base_workflow[TEXT_NODE_ID]["inputs"]["text"] == "old"
    assert base_workflow[DIMENSIONS_NODE_ID]["inputs"]["width"] == 64
    assert base_workflow["metadata"]["nested"]["values"] == [1, 2, 3]
    assert result[TEXT_NODE_ID]["inputs"]["text"] == expected_prompt
    assert result[DIMENSIONS_NODE_ID]["inputs"]["width"] == 128
    assert result[DIMENSIONS_NODE_ID]["inputs"]["height"] == 256


def test_validate_workflow_shape_raises_for_missing_required_nodes():
    workflow = _base_workflow()
    del workflow[TEXT_NODE_ID]

    with pytest.raises(WorkflowValidationError, match="missing required node"):
        validate_workflow_shape(workflow)


def test_validate_workflow_shape_rejects_non_mapping():
    with pytest.raises(WorkflowValidationError, match="must be a mapping"):
        validate_workflow_shape(["not", "a", "workflow"])


def test_legacy_workflow_setting_resolves_to_new_folder():
    resolved = resolve_workflow_path("image_z_image_turbo.json")

    assert resolved.name == "image_z_image_turbo.json"
    assert resolved.parent.name == "workflows"
    assert resolved.is_file()


def test_clear_owned_queue_deletes_prompt_ids_and_returns_without_global_clear():
    calls: list[tuple[str, dict]] = []

    def post_request(url: str, json: dict, timeout: int | float | None = None):
        calls.append((url, json))
        return _Response(ok=True)

    clear_owned_queue(
        session=object(),
        comfyui_url="http://comfyui.local",
        prompt_ids=["p1", "p2"],
        allow_global_clear=False,
        post_request=post_request,
    )

    assert calls == [
        ("http://comfyui.local/queue", {"delete": ["p1", "p2"]}),
    ]


def test_clear_owned_queue_fallback_to_global_clear_when_allowed():
    calls: list[tuple[str, dict]] = []

    def post_request(url: str, json: dict, timeout: int | float | None = None):
        calls.append((url, json))
        if len(calls) == 1:
            return _Response(ok=False)
        return _Response(ok=True)

    clear_owned_queue(
        session=object(),
        comfyui_url="http://comfyui.local",
        prompt_ids=["p1", "p2"],
        allow_global_clear=True,
        post_request=post_request,
    )

    assert calls == [
        ("http://comfyui.local/queue", {"delete": ["p1", "p2"]}),
        ("http://comfyui.local/queue", {"clear": True}),
    ]


def test_clear_owned_queue_raises_if_prompt_delete_fails_without_global_clear():
    calls: list[tuple[str, dict]] = []

    def post_request(url: str, json: dict, timeout: int | float | None = None):
        calls.append((url, json))
        return _Response(ok=False)

    with pytest.raises(
        RuntimeError, match="Failed to delete owned ComfyUI queue entries."
    ):
        clear_owned_queue(
            session=object(),
            comfyui_url="http://comfyui.local",
            prompt_ids=["p1"],
            allow_global_clear=False,
            post_request=post_request,
        )

    assert calls == [("http://comfyui.local/queue", {"delete": ["p1"]})]


def test_interrupt_owned_work_skips_when_no_prompt_and_posts_when_present():
    calls: list[tuple[str, dict]] = []

    def post_request(
        url: str, json: dict | None = None, timeout: int | float | None = None
    ):
        calls.append((url, json))
        return _Response(ok=True)

    interrupt_owned_work(
        session=object(),
        comfyui_url="http://comfyui.local",
        current_prompt_id="",
        post_request=post_request,
    )

    interrupt_owned_work(
        session=object(),
        comfyui_url="http://comfyui.local",
        current_prompt_id="p-run",
        post_request=post_request,
    )

    assert calls == [("http://comfyui.local/interrupt", None)]


def test_cancel_comfyui_work_runs_interrupt_and_then_clear(monkeypatch):
    calls: list[str] = []

    def fake_interrupt(session, comfyui_url, current_prompt_id, post_request=None):
        calls.append(f"interrupt:{comfyui_url}:{current_prompt_id}")

    def fake_clear(
        session, comfyui_url, prompt_ids, allow_global_clear, post_request=None
    ):
        calls.append(f"clear:{comfyui_url}:{sorted(prompt_ids)}:{allow_global_clear}")

    monkeypatch.setattr(
        "quote_image_generator.get_image.interrupt_owned_work", fake_interrupt
    )
    monkeypatch.setattr("quote_image_generator.get_image.clear_owned_queue", fake_clear)

    def fake_queue_get(url: str, timeout: int | float | None = None):
        if url == "http://comfyui.local/queue":
            return _PayloadResponse(
                {
                    "queue_running": [[0, "current", "other fields"], ["not-running"]],
                    "queue_pending": [[1, "pending"]],
                }
            )
        raise RuntimeError(f"Unexpected GET call: {url}")

    cancel_comfyui_work(
        session=object(),
        comfyui_url="http://comfyui.local",
        current_prompt_id="current",
        pending_prompt_ids=["old", "other"],
        allow_global_queue_clear=True,
        queue_get_request=fake_queue_get,
    )

    assert calls == [
        "interrupt:http://comfyui.local:current",
        "clear:http://comfyui.local:['old', 'other']:True",
    ]


def test_cancel_comfyui_work_interrupts_only_when_current_prompt_is_running(
    monkeypatch,
):
    calls: list[str] = []

    def fake_interrupt(session, comfyui_url, current_prompt_id, post_request=None):
        calls.append(f"interrupt:{comfyui_url}:{current_prompt_id}")

    def fake_clear(
        session, comfyui_url, prompt_ids, allow_global_clear, post_request=None
    ):
        calls.append(f"clear:{comfyui_url}:{sorted(prompt_ids)}:{allow_global_clear}")

    def fake_queue_get(url: str, timeout: int | float | None = None):
        if url == "http://comfyui.local/queue":
            return _PayloadResponse(
                {
                    "queue_running": [[0, "current", "running"], [1, "other"]],
                    "queue_pending": [[2, "waiting"]],
                }
            )
        raise RuntimeError(f"Unexpected GET call: {url}")

    monkeypatch.setattr(
        "quote_image_generator.get_image.interrupt_owned_work", fake_interrupt
    )
    monkeypatch.setattr("quote_image_generator.get_image.clear_owned_queue", fake_clear)

    cancel_comfyui_work(
        session=object(),
        comfyui_url="http://comfyui.local",
        current_prompt_id="current",
        pending_prompt_ids=["current"],
        allow_global_queue_clear=True,
        queue_get_request=fake_queue_get,
    )

    assert calls == [
        "interrupt:http://comfyui.local:current",
        "clear:http://comfyui.local:['current']:True",
    ]


def test_cancel_comfyui_work_skips_interrupt_when_prompt_is_only_queued(monkeypatch):
    calls: list[str] = []

    def fake_interrupt(session, comfyui_url, current_prompt_id, post_request=None):
        calls.append(f"interrupt:{comfyui_url}:{current_prompt_id}")

    def fake_clear(
        session, comfyui_url, prompt_ids, allow_global_clear, post_request=None
    ):
        calls.append(f"clear:{comfyui_url}:{sorted(prompt_ids)}:{allow_global_clear}")

    def fake_queue_get(url: str, timeout: int | float | None = None):
        if url == "http://comfyui.local/queue":
            return _PayloadResponse(
                {
                    "queue_running": [[0, "other"], [1, "another_running"]],
                    "queue_pending": [[2, "current"], [3, "future"]],
                }
            )
        raise RuntimeError(f"Unexpected GET call: {url}")

    monkeypatch.setattr(
        "quote_image_generator.get_image.interrupt_owned_work", fake_interrupt
    )
    monkeypatch.setattr("quote_image_generator.get_image.clear_owned_queue", fake_clear)

    cancel_comfyui_work(
        session=object(),
        comfyui_url="http://comfyui.local",
        current_prompt_id="current",
        pending_prompt_ids=["current"],
        allow_global_queue_clear=True,
        queue_get_request=fake_queue_get,
    )

    assert calls == ["clear:http://comfyui.local:['current']:True"]


def test_get_queue_snapshot_parses_sequence_entries_from_queue_payload():
    class _FakeSession:
        def get(self, url: str, timeout: int | float | None = None):
            if url == "http://comfyui.local/queue":
                return _PayloadResponse(
                    {
                        "queue_running": [
                            [0, "running-1", "a"],
                            (1, "running-2", "b"),
                            {"prompt_id": "running-map"},
                        ],
                        "queue_pending": [
                            [2, "pending-1", "x"],
                            ["bad", "", "y"],
                            (3, "pending-2"),
                        ],
                    }
                )
            raise RuntimeError(f"Unexpected GET call: {url}")

    snapshot = get_queue_snapshot(
        session=_FakeSession(),
        comfyui_url="http://comfyui.local",
    )

    assert snapshot["running"] == {"running-1", "running-2", "running-map"}
    assert snapshot["pending"] == {"pending-1", "pending-2"}


def test_is_prompt_running_uses_real_queue_payload_shape():
    class _FakeSession:
        def get(self, url: str, timeout: int | float | None = None):
            if url == "http://comfyui.local/queue":
                return _PayloadResponse(
                    {
                        "queue_running": [[0, "running", "a"], [1, "other-running"]],
                        "queue_pending": [[2, "queued-target"]],
                    }
                )
            raise RuntimeError(f"Unexpected GET call: {url}")

    assert is_prompt_running(
        session=_FakeSession(),
        comfyui_url="http://comfyui.local",
        prompt_id="running",
    )
    assert not is_prompt_running(
        session=_FakeSession(),
        comfyui_url="http://comfyui.local",
        prompt_id="queued-target",
    )


def test_fit_text_block_reduces_font_size_when_space_constrained():
    image = Image.new("RGB", (400, 240), "black")
    draw = ImageDraw.Draw(image)

    font, wrapped_text = _fit_text_block(
        draw=draw,
        text="This is intentionally long text that requires wrapping and font-size reduction.",
        max_width=180,
        max_height=80,
        starting_font_size=72,
        min_font_size=10,
    )

    assert font.size < 72
    assert "\n" in wrapped_text


def test_fit_author_font_size_uses_legacy_0_8_ratio_for_author_width():
    max_quote_width = 250
    author_text = "—"
    full_ratio_font_size = None
    reduced_ratio_font_size = None

    for repeat in range(1, 160):
        candidate = author_text + ("W" * repeat)
        full_ratio = _fit_author_font_size(
            author_text=candidate,
            max_width=max_quote_width,
            starting_font_size=72,
            width_ratio=1.0,
        )
        reduced_ratio = _fit_author_font_size(
            author_text=candidate,
            max_width=max_quote_width,
            starting_font_size=72,
            width_ratio=0.8,
        )
        if full_ratio > reduced_ratio >= 10 and full_ratio >= 11:
            full_ratio_font_size = full_ratio
            reduced_ratio_font_size = reduced_ratio
            break

    assert full_ratio_font_size is not None
    assert reduced_ratio_font_size is not None
    assert full_ratio_font_size < 72
    assert reduced_ratio_font_size < 72
    assert reduced_ratio_font_size < full_ratio_font_size
    assert full_ratio_font_size - reduced_ratio_font_size >= 1


def test_flatten_rgba_to_rgb_uses_background_for_transparency():
    transparent = Image.new("RGBA", (2, 2), (255, 0, 0, 0))
    flattened = flatten_rgba_to_rgb(transparent, background_color=(10, 20, 30))

    assert flattened.mode == "RGB"
    assert flattened.getpixel((0, 0)) == (10, 20, 30)


def test_overlay_text_on_image_converts_rgba_input_to_jpeg(tmp_path):
    transparent_image = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    input_path = tmp_path / "input.png"
    output_path = tmp_path / "output.jpeg"
    transparent_image.save(input_path)

    assert overlay_text_on_image(
        image_path=input_path,
        output_path=output_path,
        quote="A long quote that forces text wrapping in the renderer for image generation.",
        author="Author Name",
    )

    with Image.open(output_path) as output_image:
        assert output_image.mode == "RGB"
        assert output_image.format == "JPEG"
        assert output_image.size == (300, 300)
