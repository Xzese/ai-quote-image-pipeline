from __future__ import annotations

import json
import os
import re
import signal
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .config import (
    ConfigurationError,
    get_env_int,
    get_env_str,
    get_required_file_path,
    load_project_env,
)
from openai import OpenAI
from .quote_validation import QuoteValidationError, validate_quote_records
from transformers import AutoTokenizer


RESET_PROMPTS_AND_HASHTAGS = False
MAX_PROMPT_RETRIES = 5
MAX_HASHTAG_RETRIES = 5
MAX_PROMPT_TOKENS = 50
MAX_HASHTAGS = 20
MODEL_TIMEOUT_SECONDS = 60

DEFAULT_LM_STUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LM_STUDIO_API_KEY = "lm-studio"
DEFAULT_LM_STUDIO_MODEL = "qwen/qwen3.5-9b"
DEFAULT_LM_STUDIO_PRESET = "@local:no-thinking"
DEFAULT_PARALLEL_WORKERS = 4
DEFAULT_TOKENIZER_NAME = "bert-base-uncased"

HASHTAG_TOKEN_RE = re.compile(r"#[^\s#{}]+")

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
        raise SystemExit(130)


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


def extract_braced_content(text):
    if not text:
        return None

    match = re.search(r"\{([^{}]+)\}", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


def extract_fallback_text(text):
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
    cleaned = cleaned.replace("```", "").strip()
    cleaned = re.sub(
        r"^(prompt|answer|hashtags)\s*:\s*", "", cleaned, flags=re.IGNORECASE
    ).strip()

    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1].strip()

    return cleaned or None


def is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def _load_prompt_settings():
    load_project_env()

    base_url = get_env_str("LM_STUDIO_BASE_URL", default=DEFAULT_LM_STUDIO_BASE_URL)
    api_key = get_env_str("LM_STUDIO_API_KEY", default=DEFAULT_LM_STUDIO_API_KEY)
    model_name = get_env_str("LM_STUDIO_MODEL", default=DEFAULT_LM_STUDIO_MODEL)
    preset = get_env_str("LM_STUDIO_PRESET", default=DEFAULT_LM_STUDIO_PRESET)
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
    if not preset:
        raise ConfigurationError(
            "LM_STUDIO_PRESET is not set and no default is available."
        )

    if parallel_workers is None:
        raise ConfigurationError("LM_STUDIO_PARALLEL_WORKERS could not be resolved.")
    if parallel_workers < 1:
        raise ConfigurationError("LM_STUDIO_PARALLEL_WORKERS must be at least 1.")

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model_name": model_name,
        "preset": preset,
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


def save_json(data, quotes_file_path) -> None:
    with file_lock:
        tmp_path = f"{quotes_file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as json_file:
            json.dump(data, json_file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, quotes_file_path)


def call_model(messages, *, client, model_name: str, preset: str) -> str:
    if stop_event.is_set():
        raise InterruptedError("Shutdown requested before model call")

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.7,
            stream=False,
            max_tokens=150,
            timeout=MODEL_TIMEOUT_SECONDS,
            extra_body={
                "preset": preset,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
    except Exception as exc:
        raise RuntimeError(
            f"LM Studio request failed for model {model_name!r}: {exc}"
        ) from exc

    choices = getattr(completion, "choices", None)
    if not choices:
        raise RuntimeError("LM Studio response is missing choices.")

    content = getattr(choices[0].message, "content", None)
    if content is None:
        raise RuntimeError("LM Studio response content is empty.")

    return content


def _extract_hashtag_tokens(hashtags: str) -> list[str]:
    return HASHTAG_TOKEN_RE.findall(hashtags)


def generate_prompt(
    item, item_index, *, client, tokenizer, model_name: str, preset: str
):
    for attempt in range(1, MAX_PROMPT_RETRIES + 1):
        if stop_event.is_set():
            log(f"Item {item_index}: prompt generation skipped due to shutdown")
            return None

        chat_message = (
            "Generate an image generation prompt for a diffusion model that matches "
            "the tone of the following quote. Do not mention the author. Do not mention "
            "text overlays. Do not include any people. Return only the prompt in curly braces. "
            "The prompt must be at or below 50 tokens.\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        try:
            text_response = call_model(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create concise image prompts. Return only one prompt enclosed in curly braces. "
                            "Example: {misty pine forest at dawn, soft light, calm atmosphere}. "
                            "Do not add explanations. Do not add markdown. No people. Maximum 50 tokens."
                        ),
                    },
                    {"role": "user", "content": chat_message},
                ],
                client=client,
                model_name=model_name,
                preset=preset,
            )
        except Exception as error:
            log(
                f"Item {item_index}, prompt attempt {attempt}: model call failed: {error}"
            )
            continue

        prompt = extract_braced_content(text_response) or extract_fallback_text(
            text_response
        )

        if not prompt:
            log(f"Item {item_index}, prompt attempt {attempt}: no valid prompt found")
            log("RAW MODEL OUTPUT:")
            log("-" * 60)
            log(text_response)
            log("-" * 60)
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

        log(
            f"Item {item_index}, prompt attempt {attempt}: rejected "
            f"(tokens={number_of_tokens}, contains_double_quote={'\"' in prompt}) "
            f"response={text_response!r}"
        )

    return None


def generate_hashtags(item, item_index, *, client, model_name: str, preset: str):
    for attempt in range(1, MAX_HASHTAG_RETRIES + 1):
        if stop_event.is_set():
            log(f"Item {item_index}: hashtag generation skipped due to shutdown")
            return None

        chat_message = (
            f"Generate a string of {MAX_HASHTAGS} or fewer instagram hashtags. "
            "They must be related to the quote and/or the author. "
            "They can also reference AI art in some of them. "
            "Please return the result in the format:\n"
            "hashtags: {#hashtag1 #hashtag2 ... }\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        try:
            text_response = call_model(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create Instagram hashtags. Return only the hashtags enclosed in curly braces. "
                            "Example: {#inspiration #quotes #wisdom #aiart}. "
                            "Do not add explanations. Do not add markdown. Use 20 or fewer hashtags."
                        ),
                    },
                    {"role": "user", "content": chat_message},
                ],
                client=client,
                model_name=model_name,
                preset=preset,
            )
        except Exception as error:
            log(
                f"Item {item_index}, hashtag attempt {attempt}: model call failed: {error}"
            )
            continue

        hashtags = extract_braced_content(text_response) or extract_fallback_text(
            text_response
        )

        if not hashtags:
            log(f"Item {item_index}, hashtag attempt {attempt}: empty model response")
            continue

        hashtag_tokens = _extract_hashtag_tokens(hashtags)
        if not hashtag_tokens:
            log(
                f"Item {item_index}, hashtag attempt {attempt}: no hashtags extracted from response"
            )
            continue

        if len(hashtag_tokens) <= MAX_HASHTAGS:
            return " ".join(hashtag_tokens)

        log(
            f"Item {item_index}, hashtag attempt {attempt}: rejected "
            f"(hashtags={len(hashtag_tokens)}) response={text_response!r}"
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

    try:
        settings = _load_prompt_settings()
        quote_data = _load_quote_data(settings["quotes_file_path"])
        tokenizer = create_tokenizer()
        client = create_openai_client(settings["base_url"], settings["api_key"])
    except ConfigurationError as exc:
        log(f"Configuration error: {exc}")
        return 1
    except ValueError as exc:
        log(f"Input error: {exc}")
        return 1
    except Exception as exc:
        log(f"Failed to initialize runtime dependencies: {exc}")
        return 1

    executor = ThreadPoolExecutor(max_workers=settings["parallel_workers"])
    futures = {}

    try:
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

        while pending:
            if stop_event.is_set():
                # Cancel anything not started yet
                for future in list(pending):
                    if future.cancel():
                        idx = futures[future]
                        log(f"Cancelled pending Item {idx}")
                        pending.remove(future)

                if force_exit_event.is_set():
                    break

            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)

            for future in done:
                index = futures[future]
                try:
                    item_index, changed = future.result()
                    log(f"Finished Item {item_index} (changed={changed})")
                except InterruptedError:
                    log(f"Stopped Item {index} due to shutdown request")
                except Exception as error:
                    log(f"An error occurred in Item {index}: {error}")

        try:
            save_json(quote_data, settings["quotes_file_path"])
        except Exception as exc:
            log(f"Final save failed: {exc}")
            return 1

        if stop_event.is_set():
            log("Graceful shutdown complete.")
        else:
            log("All done.")

    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
