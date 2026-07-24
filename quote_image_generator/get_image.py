from __future__ import annotations

import copy
import json
import signal
import textwrap
import threading
import time
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

import requests
from PIL import Image, ImageDraw, ImageFont

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quote_image_generator.config import (
    ConfigurationError,
    ensure_directory,
    get_env_bool,
    get_env_str,
    get_required_file_path,
    load_project_env,
    resolve_repo_path,
)
from quote_image_generator.quote_validation import (
    QuoteValidationError,
    safe_output_file_path,
    validate_quote_records,
)

FONT_FILE = resolve_repo_path("assets/fonts/Alegreya-VariableFont.ttf")
WIDTH = 1024
HEIGHT = 1024
STEPS = 10
CFG = 1
POLL_INTERVAL = 2
TIMEOUT_SECONDS = 300

TEXT_NODE_ID = "57:27"
DIMENSIONS_NODE_ID = "57:13"
SAMPLER_NODE_ID = "57:3"

QueuePostFn = Callable[..., requests.Response]
QueueGetFn = Callable[..., requests.Response]
SleepFn = Callable[[float], None]


class WorkflowValidationError(ValueError):
    """Raised when the workflow file does not contain the required nodes."""


class ComfyUIError(RuntimeError):
    """Raised when ComfyUI API calls fail."""


def _required_str_env(name: str) -> str:
    value = get_env_str(name)
    if not value:
        raise ConfigurationError(f"{name} is required.")
    return value


def _safe_default_bool(name: str, default: bool = False) -> bool:
    try:
        return bool(get_env_bool(name, default=default))
    except ConfigurationError as exc:
        raise ConfigurationError(f"{name} must be true/false.") from exc


def validate_workflow_shape(base_workflow: Mapping[str, Any]) -> None:
    if not isinstance(base_workflow, Mapping):
        raise WorkflowValidationError("Workflow must be a mapping.")

    required_nodes: dict[str, tuple[str, ...]] = {
        TEXT_NODE_ID: ("text",),
        DIMENSIONS_NODE_ID: ("width", "height"),
        SAMPLER_NODE_ID: ("steps", "cfg", "seed"),
    }

    for node_id, required_path in required_nodes.items():
        if node_id not in base_workflow:
            raise WorkflowValidationError(
                f"Workflow missing required node {node_id!r}."
            )

        node_data = base_workflow.get(node_id)
        if not isinstance(node_data, Mapping):
            raise WorkflowValidationError(f"Node {node_id!r} must be an object.")
        if isinstance(required_path, tuple):
            inputs = node_data.get("inputs")
            if not isinstance(inputs, Mapping):
                raise WorkflowValidationError(
                    f"Node {node_id!r} must contain an inputs object."
                )
            missing_inputs = [field for field in required_path if field not in inputs]
            if missing_inputs:
                raise WorkflowValidationError(
                    f"Node {node_id!r} is missing required fields {missing_inputs!r}."
                )


def build_workflow(
    base_workflow: Mapping[str, Any],
    prompt_text: str,
    width: int = WIDTH,
    height: int = HEIGHT,
    steps: int = STEPS,
    cfg: int = CFG,
    seed: int | None = None,
) -> dict[str, Any]:
    validate_workflow_shape(base_workflow)
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError("prompt_text must be a non-empty string.")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers.")

    workflow = copy.deepcopy(base_workflow)

    if seed is None or seed == -1:
        seed = int(time.time_ns() % 9_007_199_254_740_991)

    workflow[TEXT_NODE_ID]["inputs"]["text"] = prompt_text
    workflow[DIMENSIONS_NODE_ID]["inputs"]["width"] = width
    workflow[DIMENSIONS_NODE_ID]["inputs"]["height"] = height
    workflow[SAMPLER_NODE_ID]["inputs"]["steps"] = steps
    workflow[SAMPLER_NODE_ID]["inputs"]["cfg"] = cfg
    workflow[SAMPLER_NODE_ID]["inputs"]["seed"] = seed

    return workflow


def queue_prompt(
    session: requests.Session,
    comfyui_url: str,
    workflow: Mapping[str, Any],
    post_request: QueuePostFn | None = None,
) -> str:
    post = post_request or session.post
    try:
        response = post(f"{comfyui_url}/prompt", json={"prompt": workflow}, timeout=300)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise ComfyUIError("Failed to queue ComfyUI prompt.") from exc

    prompt_id = payload.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        raise ComfyUIError(f"ComfyUI response missing prompt_id: {payload!r}")
    return prompt_id


def get_history(
    session: requests.Session,
    comfyui_url: str,
    prompt_id: str,
    get_request: QueueGetFn | None = None,
) -> Mapping[str, Any]:
    get = get_request or session.get
    try:
        response = get(f"{comfyui_url}/history/{prompt_id}", timeout=300)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise ComfyUIError(
            f"Failed to read ComfyUI history for prompt {prompt_id!r}."
        ) from exc


def wait_for_image(
    session: requests.Session,
    comfyui_url: str,
    prompt_id: str,
    timeout_seconds: int = TIMEOUT_SECONDS,
    poll_interval: int = POLL_INTERVAL,
    stop_event: threading.Event | None = None,
    get_request: QueueGetFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> Mapping[str, Any]:
    end_time = time.time() + timeout_seconds

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt(
                f"Stop requested while waiting for prompt {prompt_id!r}."
            )

        history = get_history(
            session=session,
            comfyui_url=comfyui_url,
            prompt_id=prompt_id,
            get_request=get_request,
        )
        entry = history.get(prompt_id)
        if not isinstance(entry, Mapping):
            sleep_fn(poll_interval)
            continue

        images = []
        for node_output in entry.get("outputs", {}).values():
            if not isinstance(node_output, Mapping):
                continue
            if isinstance(node_output.get("images"), list):
                images.extend(node_output.get("images", []))

        if images:
            image_info = images[0]
            if isinstance(image_info, Mapping):
                return image_info

        sleep_fn(poll_interval)

    raise TimeoutError(f"Timed out waiting for image for prompt_id {prompt_id}.")


def download_image(
    session: requests.Session,
    comfyui_url: str,
    image_info: Mapping[str, Any],
    save_path: Path,
    get_request: QueueGetFn | None = None,
) -> None:
    get = get_request or session.get
    filename = image_info.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError(f"Invalid image record: missing filename. {image_info!r}")

    params = {
        "filename": filename,
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    }

    try:
        response = get(f"{comfyui_url}/view", params=params, timeout=300)
        response.raise_for_status()
    except Exception as exc:
        raise ComfyUIError(
            f"Failed to download image {filename!r} from ComfyUI."
        ) from exc

    temp_path = save_path.with_suffix(".part")
    with temp_path.open("wb") as out:
        out.write(response.content)
    temp_path.replace(save_path)

    if not save_path.is_file() or save_path.stat().st_size <= 0:
        raise IOError(f"Image download for {filename!r} did not write any data.")


def flatten_rgba_to_rgb(
    input_image: Image.Image, background_color: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    if input_image.mode == "RGB":
        return input_image

    if input_image.mode == "P":
        if "transparency" in input_image.info:
            source = input_image.convert("RGBA")
        else:
            return input_image.convert("RGB")
    elif input_image.mode in {"RGBA", "LA"}:
        source = input_image.convert("RGBA")
    else:
        return input_image.convert("RGB")

    alpha = source.getchannel("A")
    flattened = Image.new("RGB", input_image.size, background_color)
    flattened.paste(source, mask=alpha)
    return flattened


def _fit_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(current + [word]) if current else word
        if font.getlength(candidate) <= max_width:
            current.append(word)
            continue

        if not current:
            fallback_width = max(1, max_width // max(1, max(1, len(word))))
            wrapped = textwrap.wrap(word, width=fallback_width)
            lines.extend(wrapped)
            continue

        lines.append(" ".join(current))
        current = [word]

    if current:
        lines.append(" ".join(current))
    return lines


def _fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    starting_font_size: int = 72,
    min_font_size: int = 10,
) -> tuple[ImageFont.FreeTypeFont, str]:
    if not text.strip():
        raise ValueError("Text cannot be blank.")

    font_size = starting_font_size
    while font_size >= min_font_size:
        font = ImageFont.truetype(str(FONT_FILE), font_size, encoding="unicode")
        lines = _fit_wrapped_lines(draw, text, font, max_width)
        text_block = "\n".join(lines)

        bounds = draw.multiline_textbbox((0, 0), text_block, font=font, spacing=4)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]

        if text_width <= max_width and text_height <= max_height:
            return font, text_block

        font_size -= 1

    raise ValueError("Could not fit wrapped text into the target area.")


def _fit_author_font_size(
    author_text: str,
    max_width: int,
    starting_font_size: int = 72,
    min_font_size: int = 10,
    width_ratio: float = 0.8,
) -> int:
    if not author_text.strip():
        raise ValueError("Author text cannot be blank.")

    author_font_size = starting_font_size
    while True:
        author_font = ImageFont.truetype(str(FONT_FILE), author_font_size)
        if (
            author_font.getlength(author_text) <= max_width * width_ratio
            or author_font_size <= min_font_size
        ):
            return author_font_size
        author_font_size -= 1


def _parse_queue_prompt_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()

    prompt_ids: set[str] = set()
    for item in value:
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, Mapping):
            if not isinstance(item.get("prompt"), str) and not isinstance(
                item.get("prompt_id"), str
            ):
                continue

            candidate = (item.get("prompt") or item.get("prompt_id") or "").strip()
        elif isinstance(item, (list, tuple)):
            if len(item) < 2 or not isinstance(item[1], str):
                continue
            candidate = item[1].strip()
        else:
            continue

        if candidate:
            prompt_ids.add(candidate)

    return prompt_ids


def get_queue_snapshot(
    session: requests.Session,
    comfyui_url: str,
    get_request: QueueGetFn | None = None,
) -> dict[str, set[str]]:
    get = get_request or session.get
    try:
        response = get(f"{comfyui_url}/queue", timeout=30)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise ComfyUIError(
            f"Failed to read ComfyUI queue state from {comfyui_url!r}."
        ) from exc

    if not isinstance(payload, Mapping):
        raise ComfyUIError(
            "Unexpected ComfyUI queue payload shape; expected a JSON object."
        )

    return {
        "running": _parse_queue_prompt_ids(payload.get("queue_running")),
        "pending": _parse_queue_prompt_ids(payload.get("queue_pending")),
    }


def is_prompt_running(
    session: requests.Session,
    comfyui_url: str,
    prompt_id: str,
    get_request: QueueGetFn | None = None,
) -> bool:
    snapshot = get_queue_snapshot(
        session=session,
        comfyui_url=comfyui_url,
        get_request=get_request,
    )
    return prompt_id in snapshot["running"]


def resolve_workflow_path(configured_path: str | None) -> Path:
    default_path = resolve_repo_path("workflows/image_z_image_turbo.json")
    if not configured_path:
        return default_path

    resolved_path = resolve_repo_path(configured_path)
    legacy_path = resolve_repo_path("image_z_image_turbo.json")
    if resolved_path == legacy_path and not resolved_path.exists():
        return default_path
    return resolved_path


def overlay_text_on_image(
    image_path: Path | str, output_path: Path | str, quote: str, author: str
) -> bool:
    image = Image.open(image_path)
    flattened_image: Image.Image | None = None
    output_image: Image.Image | None = None
    try:
        flattened_image = flatten_rgba_to_rgb(image)
        draw = ImageDraw.Draw(flattened_image)
        image_width, image_height = flattened_image.size

        max_quote_width = int(0.82 * image_width)
        max_quote_height = int(0.6 * image_height)
        quote_font, wrapped_text = _fit_text_block(
            draw=draw,
            text=quote,
            max_width=max_quote_width,
            max_height=max_quote_height,
            starting_font_size=72,
            min_font_size=10,
        )

        quote_box = draw.multiline_textbbox(
            (0, 0), wrapped_text, font=quote_font, spacing=4
        )
        text_width = quote_box[2] - quote_box[0]
        text_height = quote_box[3] - quote_box[1]

        text_x = (image_width - text_width) / 2
        text_y = ((image_height - text_height) / 2) - 40

        text_color = (255, 255, 255)
        outline_color = (0, 0, 0)
        offsets = (-2, 0, 2)

        for offset_x in offsets:
            for offset_y in offsets:
                if offset_x == 0 and offset_y == 0:
                    continue
                draw.multiline_text(
                    (text_x + offset_x, text_y + offset_y),
                    wrapped_text,
                    font=quote_font,
                    fill=outline_color,
                    spacing=4,
                    align="center",
                )

        draw.multiline_text(
            (text_x, text_y),
            wrapped_text,
            font=quote_font,
            fill=text_color,
            spacing=4,
            align="center",
        )

        author_text = "—" + author
        author_font_size = _fit_author_font_size(
            author_text=author_text,
            max_width=max_quote_width,
        )
        author_font = ImageFont.truetype(str(FONT_FILE), author_font_size)

        author_x = image_width * 0.9 - author_font.getlength(author_text)
        author_y = text_y + text_height + 25

        for offset_x in offsets:
            for offset_y in offsets:
                if offset_x == 0 and offset_y == 0:
                    continue
                draw.text(
                    (author_x + offset_x, author_y + offset_y),
                    author_text,
                    font=author_font,
                    fill=outline_color,
                )

        draw.text((author_x, author_y), author_text, font=author_font, fill=text_color)

        output_image = flattened_image.convert("RGB")
        output_image.save(output_path, "JPEG", quality=90)
        return True
    finally:
        if output_image is not None:
            output_image.close()
        if flattened_image is not None and flattened_image is not image:
            flattened_image.close()
        image.close()


def clear_owned_queue(
    session: requests.Session,
    comfyui_url: str,
    prompt_ids: Sequence[str],
    allow_global_clear: bool,
    post_request: QueuePostFn | None = None,
) -> None:
    post = post_request or session.post
    if not prompt_ids:
        return

    payload = {"delete": list(prompt_ids)}
    try:
        response = post(f"{comfyui_url}/queue", json=payload, timeout=30)
        response.raise_for_status()
        return
    except Exception as exc:
        if not allow_global_clear:
            raise RuntimeError("Failed to delete owned ComfyUI queue entries.") from exc

    try:
        response = post(f"{comfyui_url}/queue", json={"clear": True}, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            "Global queue clear was attempted but failed even though COMFYUI_ALLOW_GLOBAL_QUEUE_CLEAR is enabled."
        ) from exc


def interrupt_owned_work(
    session: requests.Session,
    comfyui_url: str,
    current_prompt_id: str | None,
    post_request: QueuePostFn | None = None,
) -> None:
    post = post_request or session.post
    if not current_prompt_id:
        return

    try:
        response = post(f"{comfyui_url}/interrupt", timeout=30)
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to interrupt ComfyUI for prompt {current_prompt_id!r}."
        ) from exc


def cancel_comfyui_work(
    session: requests.Session,
    comfyui_url: str,
    current_prompt_id: str | None,
    pending_prompt_ids: Sequence[str],
    allow_global_queue_clear: bool,
    queue_get_request: QueueGetFn | None = None,
) -> None:
    if current_prompt_id:
        try:
            if is_prompt_running(
                session=session,
                comfyui_url=comfyui_url,
                prompt_id=current_prompt_id,
                get_request=queue_get_request,
            ):
                interrupt_owned_work(
                    session=session,
                    comfyui_url=comfyui_url,
                    current_prompt_id=current_prompt_id,
                )
        except Exception:
            # Queue state could not be verified; fail safe by not interrupting.
            pass

    clear_owned_queue(
        session=session,
        comfyui_url=comfyui_url,
        prompt_ids=pending_prompt_ids,
        allow_global_clear=allow_global_queue_clear,
    )


def main() -> int:
    load_project_env()
    stop_event = threading.Event()

    def _handle_stop(signum: int, frame: object | None) -> None:
        stop_event.set()
        print("\nStop requested. Cancelling ComfyUI work safely...")

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    try:
        quotes_file_path = get_required_file_path("QUOTES_FILE_PATH")
        output_image_path = ensure_directory(
            resolve_repo_path(_required_str_env("OUTPUT_IMAGE_PATH"))
        )
        overlay_image_path = ensure_directory(
            resolve_repo_path(_required_str_env("OVERLAY_OUTPUT_PATH"))
        )
        comfyui_url = (
            get_env_str("COMFYUI_URL", "http://127.0.0.1:8000")
            or "http://127.0.0.1:8000"
        )
        workflow_file_path = resolve_workflow_path(get_env_str("COMFYUI_WORKFLOW_PATH"))
        allow_global_queue_clear = _safe_default_bool(
            "COMFYUI_ALLOW_GLOBAL_QUEUE_CLEAR", default=False
        )

        with quotes_file_path.open("r", encoding="utf-8") as quote_file:
            quote_data_raw = json.load(quote_file)
        quote_data = validate_quote_records(quote_data_raw)

        with workflow_file_path.open("r", encoding="utf-8") as workflow_file:
            base_workflow = json.load(workflow_file)
        validate_workflow_shape(base_workflow)

        pending_prompt_ids: set[str] = set()
        jobs: list[tuple[int, str, str, Mapping[str, Any], Path, Path]] = []
        session = requests.Session()
        current_prompt_id: str | None = None

        try:
            for index, item in enumerate(quote_data):
                if stop_event.is_set():
                    break

                quote_id = item["_id"]
                assert isinstance(quote_id, str)
                prompt_text = item.get("prompt")
                if not isinstance(prompt_text, str) or not prompt_text.strip():
                    print(f"Skipping item {index} - no prompt")
                    continue

                output_png = safe_output_file_path(
                    output_image_path, quote_id, WIDTH, HEIGHT, "png"
                )
                output_jpg = safe_output_file_path(
                    overlay_image_path, quote_id, WIDTH, HEIGHT, "jpeg"
                )
                png_exists = output_png.is_file() and output_png.stat().st_size > 0

                if not png_exists:
                    workflow = build_workflow(
                        base_workflow=base_workflow,
                        prompt_text=f"{prompt_text} Must have a positive, high energy atmosphere.",
                        width=WIDTH,
                        height=HEIGHT,
                        steps=STEPS,
                        cfg=CFG,
                        seed=-1,
                    )
                    prompt_id = queue_prompt(
                        session=session, comfyui_url=comfyui_url, workflow=workflow
                    )
                    pending_prompt_ids.add(prompt_id)
                    jobs.append(
                        (index, quote_id, prompt_id, item, output_png, output_jpg)
                    )
                    print(
                        f"Queued generation for item {index} with prompt_id {prompt_id}"
                    )
                else:
                    jobs.append((index, quote_id, "", item, output_png, output_jpg))
                    print(f"Image already exists for item {index}")

            for index, quote_id, prompt_id, item, output_png, output_jpg in jobs:
                if stop_event.is_set():
                    break

                try:
                    if prompt_id:
                        current_prompt_id = prompt_id
                        image_info = wait_for_image(
                            session=session,
                            comfyui_url=comfyui_url,
                            prompt_id=prompt_id,
                            stop_event=stop_event,
                        )
                        download_image(
                            session=session,
                            comfyui_url=comfyui_url,
                            image_info=image_info,
                            save_path=output_png,
                        )
                        pending_prompt_ids.discard(prompt_id)
                        current_prompt_id = None
                        print(f"Image generated for item {index}")

                    if not output_jpg.is_file() and output_png.is_file():
                        if overlay_text_on_image(
                            output_png, output_jpg, item["content"], item["author"]
                        ):
                            print(f"Added overlay for item {index}")
                    else:
                        print(f"Overlay already exists for item {index}")
                except KeyboardInterrupt:
                    current_prompt_id = prompt_id
                    raise
                except Exception as error:
                    print(f"An error occurred while processing item {index}: {error}")
        finally:
            if stop_event.is_set():
                try:
                    cancel_comfyui_work(
                        session=session,
                        comfyui_url=comfyui_url,
                        current_prompt_id=current_prompt_id,
                        pending_prompt_ids=list(pending_prompt_ids),
                        allow_global_queue_clear=allow_global_queue_clear,
                    )
                    print("Stopped safely.")
                except Exception as error:
                    print(f"Failed to cancel ComfyUI work: {error}")
            session.close()

        return 0
    except QuoteValidationError as exc:
        print(f"Invalid quote payload: {exc}")
        return 1
    except WorkflowValidationError as exc:
        print(f"Invalid workflow shape: {exc}")
        return 1
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Unhandled error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
