from __future__ import annotations

import json
import inspect
import os
import re
import signal
import threading
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI

from quote_image_generator.config import (
    ConfigurationError,
    get_env_int,
    get_env_str,
    get_required_file_path,
    load_project_env,
)
from quote_image_generator.quote_validation import (
    QuoteValidationError,
    validate_quote_records,
)
from transformers import AutoTokenizer


RESET_PROMPTS_AND_HASHTAGS = False
MAX_PROMPT_RETRIES = 5
MAX_HASHTAG_RETRIES = 5
MAX_PROMPT_TOKENS = 50
MAX_HASHTAGS = 20
MODEL_TIMEOUT_SECONDS = 60
NATIVE_API_TIMEOUT_SECONDS = 30
DOWNLOAD_POLL_SECONDS = 2
DOWNLOAD_PROGRESS_PERCENT_STEP = 10
MODEL_LIST_WAIT_RETRIES = 5
MODEL_READINESS_RETRY_ATTEMPTS = 3
MODEL_READINESS_RETRY_DELAY_SECONDS = 1

DEFAULT_LM_STUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LM_STUDIO_API_KEY = "lm-studio"
DEFAULT_LM_STUDIO_MODEL = "qwen/qwen3.5-9b"
DEFAULT_LM_STUDIO_PRESET = ""
DEFAULT_LM_STUDIO_CONTEXT_LENGTH = 8192
DEFAULT_PARALLEL_WORKERS = 4
DEFAULT_TOKENIZER_NAME = "bert-base-uncased"

HASHTAG_TOKEN_RE = re.compile(r"^#[A-Za-z0-9_]+$")

READINESS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "readiness_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
            "additionalProperties": False,
        },
    },
}

PROMPT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "image_prompt_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "minLength": 1}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
}

HASHTAGS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "hashtags_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "hashtags": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "pattern": r"^#[A-Za-z0-9_]+$",
                    },
                    "minItems": 1,
                    "maxItems": MAX_HASHTAGS,
                }
            },
            "required": ["hashtags"],
            "additionalProperties": False,
        },
    },
}

file_lock = threading.Lock()
print_lock = threading.Lock()

stop_event = threading.Event()
force_exit_event = threading.Event()
_signal_handlers_installed = False


def log(message: str) -> None:
    with print_lock:
        print(message, flush=True)


def handle_sigint(signum, frame):
    if not stop_event.is_set():
        log(
            "\nCtrl+C received. Graceful shutdown started: cancelling pending work, waiting for running tasks to finish..."
        )
        stop_event.set()
    else:
        force_exit_event.set()
        log("\nSecond Ctrl+C received. Exiting immediately.")
        os._exit(130)


def install_signal_handlers() -> None:
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)
    _signal_handlers_installed = True


def create_openai_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def create_tokenizer(tokenizer_name: str = DEFAULT_TOKENIZER_NAME) -> AutoTokenizer:
    return AutoTokenizer.from_pretrained(tokenizer_name)


def is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def derive_native_api_base_url(openai_base_url: str) -> str:
    normalized = openai_base_url.rstrip("/")
    if not normalized.endswith("/v1"):
        raise ConfigurationError(
            "LM_STUDIO_NATIVE_API_BASE_URL is not set, and LM_STUDIO_BASE_URL "
            "does not end in /v1 so the native API URL cannot be derived."
        )
    return f"{normalized[:-3]}/api/v1"


def _load_prompt_settings():
    load_project_env()

    base_url = get_env_str("LM_STUDIO_BASE_URL", default=DEFAULT_LM_STUDIO_BASE_URL)
    native_api_base_url = get_env_str("LM_STUDIO_NATIVE_API_BASE_URL")
    api_key = get_env_str("LM_STUDIO_API_KEY", default=DEFAULT_LM_STUDIO_API_KEY)
    model_name = get_env_str("LM_STUDIO_MODEL", default=DEFAULT_LM_STUDIO_MODEL)
    preset = get_env_str("LM_STUDIO_PRESET", default=DEFAULT_LM_STUDIO_PRESET)
    context_length = get_env_int(
        "LM_STUDIO_CONTEXT_LENGTH", default=DEFAULT_LM_STUDIO_CONTEXT_LENGTH
    )
    parallel_workers = get_env_int(
        "LM_STUDIO_PARALLEL_WORKERS", default=DEFAULT_PARALLEL_WORKERS
    )
    quotes_file_path = get_required_file_path("QUOTES_FILE_PATH")

    if not base_url:
        raise ConfigurationError(
            "LM_STUDIO_BASE_URL is not set and no default is available."
        )
    if not api_key:
        raise ConfigurationError(
            "LM_STUDIO_API_KEY is not set and no default is available."
        )
    if not model_name:
        raise ConfigurationError(
            "LM_STUDIO_MODEL is not set and no default is available."
        )
    model_name = _normalize_model_name(model_name)
    if not model_name:
        raise ConfigurationError("LM_STUDIO_MODEL is not a valid LM Studio model key.")
    if not native_api_base_url:
        native_api_base_url = derive_native_api_base_url(base_url)
    if context_length is None:
        raise ConfigurationError("LM_STUDIO_CONTEXT_LENGTH could not be resolved.")
    if context_length < 1:
        raise ConfigurationError("LM_STUDIO_CONTEXT_LENGTH must be at least 1.")
    if parallel_workers is None:
        raise ConfigurationError("LM_STUDIO_PARALLEL_WORKERS could not be resolved.")
    if parallel_workers < 1:
        raise ConfigurationError("LM_STUDIO_PARALLEL_WORKERS must be at least 1.")

    return {
        "base_url": base_url,
        "native_api_base_url": native_api_base_url.rstrip("/"),
        "api_key": api_key,
        "model_name": model_name,
        "preset": preset,
        "context_length": context_length,
        "parallel_workers": parallel_workers,
        "quotes_file_path": quotes_file_path,
    }


def _load_quote_data(quotes_file_path):
    try:
        with open(quotes_file_path, "r", encoding="utf-8") as json_file:
            raw_quotes = json.load(json_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read JSON from {quotes_file_path}: {exc}") from exc

    try:
        return validate_quote_records(raw_quotes)
    except QuoteValidationError as exc:
        raise ValueError(f"Invalid quote data in {quotes_file_path}: {exc}") from exc


def _native_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _request_native_json(
    session,
    method: str,
    url: str,
    *,
    api_key: str,
    payload: dict | None = None,
) -> dict:
    try:
        response = session.request(
            method,
            url,
            headers=_native_headers(api_key),
            json=payload,
            timeout=NATIVE_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"LM Studio native API request failed ({method} {url}): {exc}"
        ) from exc

    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"LM Studio native API returned malformed JSON ({method} {url})."
        ) from exc
    if not isinstance(body, dict):
        raise RuntimeError(
            f"LM Studio native API returned a non-object response ({method} {url})."
        )
    return body


def _list_native_models(
    *, session, native_api_base_url: str, api_key: str
) -> list[dict]:
    body = _request_native_json(
        session,
        "GET",
        f"{native_api_base_url}/models",
        api_key=api_key,
    )
    models = body.get("models")
    if not isinstance(models, list) or not all(
        isinstance(model, dict) for model in models
    ):
        raise RuntimeError(
            "LM Studio native model list is malformed: expected a 'models' array."
        )
    return models


def _find_native_model(models: list[dict], model_name: str) -> dict | None:
    return next((model for model in models if model.get("key") == model_name), None)


def is_stale_lm_studio_readiness_error(exc: Exception) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).lower()
    return "model has not started loading" in message or "has been unloaded" in message


def _normalize_model_name(model_name: str) -> str:
    cleaned = model_name.strip()
    if "://" in cleaned:
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigurationError(
                "LM_STUDIO_MODEL is a URL but is not an LM Studio model page URL."
            )

        if (parsed.hostname or "").lower() not in {
            "lmstudio.ai",
            "www.lmstudio.ai",
        }:
            raise ConfigurationError(
                "LM_STUDIO_MODEL is a URL but is not an LM Studio model page URL."
            )

        path = parsed.path.lstrip("/").rstrip("/")
        if path.startswith("models/"):
            normalized = path.removeprefix("models/")
            parts = normalized.split("/")
            if len(parts) == 2 and all(parts):
                return unquote(f"{parts[0]}/{parts[1]}")

        raise ConfigurationError(
            "LM_STUDIO_MODEL URL format is invalid. Expected "
            "https://lmstudio.ai/models/<publisher>/<model>."
        )

    return cleaned


def _get_first_loaded_instance_id(loaded_instances: list[dict | str]) -> str | None:
    if not loaded_instances:
        return None

    first = loaded_instances[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        for key in ("id", "instance_id"):
            value = first.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _download_progress_message(model_name: str, status: dict) -> str:
    downloaded = status.get("downloaded_bytes")
    total = status.get("total_size_bytes")
    if (
        isinstance(downloaded, (int, float))
        and isinstance(total, (int, float))
        and total > 0
    ):
        return f"Downloading LM Studio model {model_name!r}: {downloaded / total:.0%}"
    return f"Downloading LM Studio model {model_name!r}..."


def _wait_for_model_download(
    *,
    session,
    native_api_base_url: str,
    api_key: str,
    model_name: str,
    job_id: str,
) -> None:
    last_progress_bucket = -1
    logged_without_progress = False

    while True:
        if stop_event.is_set():
            raise InterruptedError(
                "Shutdown requested while downloading LM Studio model"
            )

        status_body = _request_native_json(
            session,
            "GET",
            f"{native_api_base_url}/models/download/status/{job_id}",
            api_key=api_key,
        )
        status = status_body.get("status")
        if status == "completed":
            log(f"LM Studio model {model_name!r} download completed.")
            return
        if status in {"failed", "paused"}:
            detail = status_body.get("error") or status_body.get("message")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise RuntimeError(f"LM Studio model download {status}{suffix}")
        if status != "downloading":
            raise RuntimeError(
                f"LM Studio model download returned unknown status {status!r}."
            )

        downloaded = status_body.get("downloaded_bytes")
        total = status_body.get("total_size_bytes")
        if (
            isinstance(downloaded, (int, float))
            and isinstance(total, (int, float))
            and total > 0
        ):
            percent = max(0, min(100, int(downloaded * 100 / total)))
            progress_bucket = percent // DOWNLOAD_PROGRESS_PERCENT_STEP
            if progress_bucket > last_progress_bucket:
                log(_download_progress_message(model_name, status_body))
                last_progress_bucket = progress_bucket
        elif not logged_without_progress:
            log(_download_progress_message(model_name, status_body))
            logged_without_progress = True

        if stop_event.wait(DOWNLOAD_POLL_SECONDS):
            raise InterruptedError(
                "Shutdown requested while downloading LM Studio model"
            )


def _wait_for_model_in_list(
    *,
    session,
    native_api_base_url: str,
    api_key: str,
    model_name: str,
) -> dict | None:
    for attempt in range(MODEL_LIST_WAIT_RETRIES):
        if stop_event.is_set():
            raise InterruptedError(
                "Shutdown requested before model appeared in LM Studio model list."
            )

        model = _find_native_model(
            _list_native_models(
                session=session,
                native_api_base_url=native_api_base_url,
                api_key=api_key,
            ),
            model_name,
        )
        if model is not None:
            return model

        if attempt + 1 >= MODEL_LIST_WAIT_RETRIES:
            return None

        if stop_event.wait(DOWNLOAD_POLL_SECONDS):
            raise InterruptedError(
                "Shutdown requested before model appeared in LM Studio model list."
            )

    return None


def _load_lm_studio_model(
    *,
    session,
    native_api_base_url: str,
    api_key: str,
    model_name: str,
    context_length: int | None,
) -> str:
    payload: dict[str, str | int] = {"model": model_name}
    if context_length is not None:
        payload["context_length"] = context_length

    load_result = _request_native_json(
        session,
        "POST",
        f"{native_api_base_url}/models/load",
        api_key=api_key,
        payload=payload,
    )
    instance_id = load_result.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        instance_id = load_result.get("model_instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise RuntimeError(
            "LM Studio model load response is malformed: missing instance_id."
        )
    log(
        f"Loaded LM Studio model {model_name!r} with context length "
        f"{context_length if context_length is not None else 'default'}."
    )
    return instance_id


def ensure_lm_studio_model(
    *,
    native_api_base_url: str,
    api_key: str,
    model_name: str,
    context_length: int,
    session=None,
) -> str | None:
    session = session or requests.Session()
    normalized_model_name = _normalize_model_name(model_name)

    models = _list_native_models(
        session=session,
        native_api_base_url=native_api_base_url,
        api_key=api_key,
    )
    model = _find_native_model(models, normalized_model_name)

    if model is None:
        log(
            f"LM Studio model {normalized_model_name!r} is not downloaded. Starting download..."
        )
        download = _request_native_json(
            session,
            "POST",
            f"{native_api_base_url}/models/download",
            api_key=api_key,
            payload={"model": normalized_model_name},
        )
        status = download.get("status")
        if status == "downloading":
            job_id = download.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise RuntimeError(
                    "LM Studio model download response is malformed: missing job_id."
                )
            _wait_for_model_download(
                session=session,
                native_api_base_url=native_api_base_url,
                api_key=api_key,
                model_name=normalized_model_name,
                job_id=job_id,
            )
        elif status == "completed":
            log(f"LM Studio model {normalized_model_name!r} download completed.")
        elif status == "already_downloaded":
            log(f"LM Studio model {normalized_model_name!r} is already downloaded.")
        elif status in {"failed", "paused"}:
            detail = download.get("error") or download.get("message")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise RuntimeError(f"LM Studio model download {status}{suffix}")
        else:
            raise RuntimeError(
                f"LM Studio model download returned unknown status {status!r}."
            )

        model = _wait_for_model_in_list(
            session=session,
            native_api_base_url=native_api_base_url,
            api_key=api_key,
            model_name=normalized_model_name,
        )
        if model is None:
            log(
                f"LM Studio model {normalized_model_name!r} not yet visible in model list after download; attempting direct load."
            )
            return _load_lm_studio_model(
                session=session,
                native_api_base_url=native_api_base_url,
                api_key=api_key,
                model_name=normalized_model_name,
                context_length=context_length,
            )

    loaded_instances = model.get("loaded_instances")
    if not isinstance(loaded_instances, list):
        raise RuntimeError(
            f"LM Studio model {normalized_model_name!r} metadata is malformed: "
            "expected loaded_instances to be an array."
        )
    existing_instance_id: str | None = None
    if loaded_instances:
        existing_instance_id = _get_first_loaded_instance_id(loaded_instances)
        if existing_instance_id:
            log(
                "LM Studio model "
                f"{normalized_model_name!r} already has a loaded instance {existing_instance_id!r}."
            )
            return existing_instance_id

    max_context_length = model.get("max_context_length")
    if (
        not isinstance(max_context_length, int)
        or isinstance(max_context_length, bool)
        or max_context_length < 1
    ):
        raise RuntimeError(
            f"LM Studio model {normalized_model_name!r} metadata is malformed: "
            "max_context_length must be a positive integer."
        )

    load_context_length = min(context_length, max_context_length)
    return _load_lm_studio_model(
        session=session,
        native_api_base_url=native_api_base_url,
        api_key=api_key,
        model_name=normalized_model_name,
        context_length=load_context_length,
    )


def _unload_lm_studio_model(
    *,
    session,
    native_api_base_url: str,
    api_key: str,
    instance_id: str,
) -> None:
    unload_response = _request_native_json(
        session,
        "POST",
        f"{native_api_base_url}/models/unload",
        api_key=api_key,
        payload={"instance_id": instance_id},
    )
    unloaded_instance_id = unload_response.get("instance_id")
    if unloaded_instance_id != instance_id:
        raise RuntimeError(
            "LM Studio model unload response did not confirm the expected instance."
        )
    log(f"Unloaded LM Studio model instance {instance_id!r}.")


def _validate_lm_studio_readiness(*, client, model_name: str, preset: str) -> None:
    normalized_model_name = _normalize_model_name(model_name)
    attempt = 0
    while attempt < MODEL_READINESS_RETRY_ATTEMPTS:
        attempt += 1
        try:
            startup_response = call_model(
                messages=[
                    {
                        "role": "system",
                        "content": "Return the requested structured readiness status.",
                    },
                    {"role": "user", "content": "Confirm readiness with status ok."},
                ],
                client=client,
                model_name=normalized_model_name,
                preset=preset,
                response_format=READINESS_RESPONSE_FORMAT,
                expected_field="status",
                expected_type=str,
            )
            break
        except Exception as exc:
            if not is_stale_lm_studio_readiness_error(exc):
                raise RuntimeError(
                    f"LM Studio model/structured-output check failed: {exc}"
                ) from exc
            if attempt >= MODEL_READINESS_RETRY_ATTEMPTS:
                raise RuntimeError(
                    f"LM Studio model/structured-output check failed: {exc}"
                ) from exc
            if stop_event.wait(MODEL_READINESS_RETRY_DELAY_SECONDS):
                raise InterruptedError(
                    "Shutdown requested while waiting to retry readiness check."
                )

    if startup_response["status"] != "ok":
        raise RuntimeError(
            "LM Studio structured-output check returned an invalid readiness status."
        )


def save_json(data, quotes_file_path) -> None:
    with file_lock:
        tmp_path = f"{quotes_file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as json_file:
            json.dump(data, json_file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, quotes_file_path)


def call_model(
    messages,
    *,
    client,
    model_name: str,
    preset: str,
    response_format: dict,
    expected_field: str,
    expected_type: type,
) -> dict:
    if stop_event.is_set():
        raise InterruptedError("Shutdown requested before model call")

    try:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        if preset:
            extra_body["preset"] = preset

        completion_args = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,
            "max_tokens": 512,
            "timeout": MODEL_TIMEOUT_SECONDS,
            "extra_body": extra_body,
            "response_format": response_format,
        }

        create_fn = client.chat.completions.create
        create_signature = inspect.signature(create_fn).parameters
        if "reasoning_effort" in create_signature or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in create_signature.values()
        ):
            completion_args["reasoning_effort"] = "none"

        completion = client.chat.completions.create(
            **completion_args,
        )
    except Exception as exc:
        raise RuntimeError(
            f"LM Studio request failed for model {model_name!r}: {exc}"
        ) from exc

    choices = getattr(completion, "choices", None)
    if not choices:
        raise RuntimeError("LM Studio response is missing choices.")

    content = getattr(choices[0].message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LM Studio response content is empty.")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LM Studio response content is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LM Studio structured response must be a JSON object.")
    if expected_field not in parsed:
        raise RuntimeError(
            f"LM Studio structured response is missing {expected_field!r}."
        )
    if not isinstance(parsed[expected_field], expected_type):
        raise RuntimeError(
            f"LM Studio structured response field {expected_field!r} has the wrong type."
        )

    return parsed


def _normalize_hashtags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            return []
        hashtag = value.strip()
        if not HASHTAG_TOKEN_RE.fullmatch(hashtag):
            return []
        canonical = hashtag.casefold()
        if canonical not in seen:
            normalized.append(hashtag)
            seen.add(canonical)
    return normalized


def generate_prompt(
    item, item_index, *, client, tokenizer, model_name: str, preset: str
):
    for attempt in range(1, MAX_PROMPT_RETRIES + 1):
        if stop_event.is_set():
            return None

        chat_message = (
            "Generate an image generation prompt for a diffusion model that matches "
            "the tone of the following quote. Do not mention the author. Do not mention "
            "text overlays. Do not include any people. Return only the prompt text, one "
            "short line, no markdown, and no extra explanation.\n"
            f"The prompt must be at or below {MAX_PROMPT_TOKENS} tokens.\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        try:
            structured_response = call_model(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create concise image prompts using the requested structured output. "
                            "The prompt value must contain one short line. "
                            "Example prompt value: misty pine forest at dawn, soft light, calm atmosphere. "
                            "No explanations, markdown, or people. Maximum 50 tokens."
                        ),
                    },
                    {"role": "user", "content": chat_message},
                ],
                client=client,
                model_name=model_name,
                preset=preset,
                response_format=PROMPT_RESPONSE_FORMAT,
                expected_field="prompt",
                expected_type=str,
            )
        except Exception as error:
            log(
                f"Item {item_index}, prompt attempt {attempt}: model call failed: {error}"
            )
            continue

        prompt = structured_response["prompt"].strip()

        if not prompt:
            log(
                f"Item {item_index}, prompt attempt {attempt}: structured prompt was empty"
            )
            continue

        try:
            number_of_tokens = len(tokenizer.tokenize(prompt))
        except Exception as error:
            log(
                f"Item {item_index}, prompt attempt {attempt}: tokenization failed: {error}"
            )
            continue

        if number_of_tokens <= MAX_PROMPT_TOKENS and '"' not in prompt:
            return prompt
        contains_double_quote = '"' in prompt

        log(
            f"Item {item_index}, prompt attempt {attempt}: rejected "
            f"(tokens={number_of_tokens}, contains_double_quote={contains_double_quote}) "
            f"response={prompt!r}"
        )

    return None


def generate_hashtags(item, item_index, *, client, model_name: str, preset: str):
    for attempt in range(1, MAX_HASHTAG_RETRIES + 1):
        if stop_event.is_set():
            return None

        chat_message = (
            f"Generate a string of {MAX_HASHTAGS} or fewer instagram hashtags. "
            "They must be related to the quote and/or the author. "
            "They can also reference AI art in some of them. "
            "Return the hashtags in the requested structured output.\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        try:
            structured_response = call_model(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create Instagram hashtags using the requested structured output. "
                            "Each value must begin with # and contain only letters, numbers, or underscores. "
                            "Use 20 or fewer hashtags."
                        ),
                    },
                    {"role": "user", "content": chat_message},
                ],
                client=client,
                model_name=model_name,
                preset=preset,
                response_format=HASHTAGS_RESPONSE_FORMAT,
                expected_field="hashtags",
                expected_type=list,
            )
        except Exception as error:
            log(
                f"Item {item_index}, hashtag attempt {attempt}: model call failed: {error}"
            )
            continue

        hashtag_tokens = _normalize_hashtags(structured_response["hashtags"])
        if not hashtag_tokens:
            log(
                f"Item {item_index}, hashtag attempt {attempt}: "
                "structured hashtags were invalid"
            )
            continue

        if len(hashtag_tokens) <= MAX_HASHTAGS:
            return " ".join(hashtag_tokens)

        log(
            f"Item {item_index}, hashtag attempt {attempt}: rejected "
            f"(hashtags={len(hashtag_tokens)}) response={structured_response!r}"
        )

    return None


def process_item(
    index,
    quote_data,
    quotes_file_path,
    *,
    client,
    tokenizer,
    model_name: str,
    preset: str,
):
    if stop_event.is_set():
        return index, False

    item = quote_data[index]
    changed = False

    log(f"Generating for Item {index}")

    if ("prompt" not in item or is_blank(item["prompt"])) and not stop_event.is_set():
        try:
            prompt = generate_prompt(
                item,
                index,
                client=client,
                tokenizer=tokenizer,
                model_name=model_name,
                preset=preset,
            )
        except Exception as error:
            log(f"Item {index}: prompt generation raised unexpected error: {error}")
            prompt = None

        if prompt:
            item["prompt"] = prompt
            changed = True
            log(f"Prompt Accepted for Item {index}: {prompt}")
        elif not stop_event.is_set():
            log(
                f"Prompt generation failed for Item {index} after {MAX_PROMPT_RETRIES} attempts"
            )

    if (
        "hashtags" not in item or is_blank(item["hashtags"])
    ) and not stop_event.is_set():
        try:
            hashtags = generate_hashtags(
                item, index, client=client, model_name=model_name, preset=preset
            )
        except Exception as error:
            log(f"Item {index}: hashtag generation raised unexpected error: {error}")
            hashtags = None

        if hashtags:
            item["hashtags"] = hashtags
            changed = True
            log(f"Hashtags Accepted for Item {index}: {hashtags}")
        elif not stop_event.is_set():
            log(
                f"Hashtag generation failed for Item {index} after {MAX_HASHTAG_RETRIES} attempts"
            )

    if changed:
        try:
            save_json(quote_data, quotes_file_path)
        except Exception as error:
            log(f"Item {index}: failed to persist JSON after update: {error}")
            raise

    return index, changed


def main() -> int:
    stop_event.clear()
    force_exit_event.clear()
    install_signal_handlers()
    managed_instance_id = None
    settings = None
    executor = None
    quote_data = []

    try:
        try:
            settings = _load_prompt_settings()
            quote_data = _load_quote_data(settings["quotes_file_path"])
            managed_instance_id = ensure_lm_studio_model(
                native_api_base_url=settings["native_api_base_url"],
                api_key=settings["api_key"],
                model_name=settings["model_name"],
                context_length=settings["context_length"],
            )
            client = create_openai_client(settings["base_url"], settings["api_key"])
            _validate_lm_studio_readiness(
                client=client,
                model_name=settings["model_name"],
                preset=settings["preset"],
            )
            tokenizer = create_tokenizer()
        except ConfigurationError as exc:
            log(f"Configuration error: {exc}")
            return 1
        except ValueError as exc:
            log(f"Input error: {exc}")
            return 1
        except InterruptedError:
            log("Startup cancelled. No quote items were processed.")
            return 130
        except RuntimeError as exc:
            log(f"LM Studio error: {exc}")
            return 1
        except Exception as exc:
            log(f"Failed to initialize runtime dependencies: {exc}")
            return 1

        executor = ThreadPoolExecutor(max_workers=settings["parallel_workers"])
        futures = {}

        if RESET_PROMPTS_AND_HASHTAGS:
            log("RESET_PROMPTS_AND_HASHTAGS is True. Clearing prompts and hashtags...")
            for item in quote_data:
                if "prompt" in item:
                    item["prompt"] = ""
                if "hashtags" in item:
                    item["hashtags"] = ""

            try:
                save_json(quote_data, settings["quotes_file_path"])
            except Exception as exc:
                log(f"Failed to clear prompts and hashtags: {exc}")
                return 1

            log("Reset complete.")

        for i in range(len(quote_data)):
            if stop_event.is_set():
                break
            future = executor.submit(
                process_item,
                i,
                quote_data,
                settings["quotes_file_path"],
                client=client,
                tokenizer=tokenizer,
                model_name=settings["model_name"],
                preset=settings["preset"],
            )
            futures[future] = i

        pending = set(futures.keys())

        pending_cancel_logged = False

        while pending:
            if force_exit_event.is_set():
                break

            if stop_event.is_set() and not force_exit_event.is_set():
                if not pending_cancel_logged:
                    cancelled_count = 0
                    for future in list(pending):
                        if future.cancel():
                            cancelled_count += 1
                            pending.remove(future)

                    if cancelled_count:
                        if cancelled_count == 1:
                            log("1 pending item has been cancelled.")
                        else:
                            log(f"{cancelled_count} pending items have been cancelled.")
                    pending_cancel_logged = True

            if force_exit_event.is_set():
                break

            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)

            for future in done:
                index = futures[future]
                if stop_event.is_set():
                    try:
                        future.result()
                    except InterruptedError:
                        pass
                    except Exception as error:
                        log(f"An error occurred in Item {index}: {error}")
                    continue

                try:
                    item_index, changed = future.result()
                    log(f"Finished Item {item_index} (changed={changed})")
                except InterruptedError:
                    log(f"Stopped Item {index} due to shutdown request")
                except Exception as error:
                    log(f"An error occurred in Item {index}: {error}")

        if force_exit_event.is_set():
            log("Immediate shutdown complete.")
            log("Skipping final save due to forced shutdown.")
            return 130

        try:
            save_json(quote_data, settings["quotes_file_path"])
        except Exception as exc:
            log(f"Final save failed: {exc}")
            return 1

        if stop_event.is_set():
            log("Graceful shutdown complete.")
        else:
            log("All done.")

        return 0
    except InterruptedError:
        log("Startup cancelled. No quote items were processed.")
        return 130
    finally:
        if executor is not None:
            executor.shutdown(wait=not force_exit_event.is_set(), cancel_futures=True)

        if managed_instance_id and settings:
            try:
                _unload_lm_studio_model(
                    session=requests.Session(),
                    native_api_base_url=settings["native_api_base_url"],
                    api_key=settings["api_key"],
                    instance_id=managed_instance_id,
                )
            except Exception as exc:
                log(f"LM Studio model unload warning: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
