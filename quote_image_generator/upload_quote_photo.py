#!/usr/bin/env python3
"""Upload a single random quote image with bounded retries."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import random
import time

from .config import (
    ConfigurationError,
    get_env_int,
    get_env_str,
    load_project_env,
    resolve_repo_path,
)
from .quote_validation import (
    QuoteValidationError,
    safe_output_file_path,
    validate_quote_records,
)

from upload_photo.upload_photo import (
    create_media_container,
    publish_media_container,
    send_email_alert,
    upload_image,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BASE_SECONDS = 2.0
DEFAULT_QUOTES_FILE = "quotes.json"
DEFAULT_IMAGE_DIR = Path("output") / "images_text_overlay"
ENV_MAX_ATTEMPTS = "UPLOAD_QUOTE_MAX_ATTEMPTS"
ENV_RETRY_BASE_SECONDS = "UPLOAD_QUOTE_RETRY_BASE_SECONDS"
ENV_QUOTES_FILE_PATH = "QUOTES_FILE_PATH"
ENV_IMAGE_DIR = "OVERLAY_OUTPUT_PATH"
OUTPUT_IMAGE_WIDTH = 1024
OUTPUT_IMAGE_HEIGHT = 1024
OUTPUT_IMAGE_EXT = "jpeg"


def _post_quote_photo(file_path: str, caption: str) -> object:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    upload_url = upload_image(file_path)
    if not upload_url:
        raise RuntimeError(f"upload_image returned empty url for {file_path}")

    container_id = create_media_container(upload_url, caption)
    if not container_id:
        raise RuntimeError(f"create_media_container returned empty id for {file_path}")
    if container_id == "No Valid Token":
        raise RuntimeError("create_media_container returned No Valid Token")

    publish_result = publish_media_container(container_id)
    if not publish_result:
        raise RuntimeError(
            f"publish_media_container returned empty result for {file_path}"
        )
    if publish_result == "No Valid Token":
        raise RuntimeError("publish_media_container returned No Valid Token")

    return publish_result


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def _parse_retry_delay() -> float:
    raw = get_env_str(ENV_RETRY_BASE_SECONDS, str(DEFAULT_RETRY_BASE_SECONDS))
    assert raw is not None
    try:
        delay = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{ENV_RETRY_BASE_SECONDS} must be a numeric value, got {raw!r}."
        ) from exc

    if delay < 0:
        raise ValueError(f"{ENV_RETRY_BASE_SECONDS} must be non-negative, got {delay}.")

    return delay


def _load_quotes(quotes_file: str) -> list[dict]:
    with open(quotes_file, "r", encoding="utf-8") as json_file:
        quote_data = json.load(json_file)

    return validate_quote_records(quote_data)


def _load_caption(quote: dict) -> str:
    caption = quote.get("hashtags", "")
    return str(caption) if caption is not None else ""


def main(
    *,
    post_func=_post_quote_photo,
    alert_func=send_email_alert,
    sleep_func=time.sleep,
    randomizer=random,
) -> int:
    _configure_logging()

    try:
        load_project_env()
        quotes_file = get_env_str(ENV_QUOTES_FILE_PATH, DEFAULT_QUOTES_FILE)
        image_dir = get_env_str(ENV_IMAGE_DIR, str(DEFAULT_IMAGE_DIR))
        max_attempts = get_env_int(ENV_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)
        retry_delay = _parse_retry_delay()

        if quotes_file is None:
            raise ConfigurationError(f"{ENV_QUOTES_FILE_PATH} is required.")
        if image_dir is None:
            raise ConfigurationError(f"{ENV_IMAGE_DIR} is required.")
        if max_attempts is None:
            raise ConfigurationError(
                f"{ENV_MAX_ATTEMPTS} resolved to an invalid value."
            )
        if max_attempts < 1:
            raise ValueError(
                f"{ENV_MAX_ATTEMPTS} must be at least 1, got {max_attempts}."
            )

        resolved_quotes_file = resolve_repo_path(quotes_file)
        resolved_image_dir = resolve_repo_path(image_dir)

        quote_records = _load_quotes(str(resolved_quotes_file))
        if len(quote_records) < 1:
            raise ValueError(f"No usable quotes found in {resolved_quotes_file}.")

        quote = randomizer.choice(quote_records)
        file_path = safe_output_file_path(
            resolved_image_dir,
            quote["_id"],
            OUTPUT_IMAGE_WIDTH,
            OUTPUT_IMAGE_HEIGHT,
            OUTPUT_IMAGE_EXT,
        )
        caption = _load_caption(quote)

        for attempt in range(1, max_attempts + 1):
            try:
                post_func(str(file_path), caption)
                LOGGER.info("Posted quote image successfully.")
                return 0
            except Exception as exc:
                LOGGER.exception(
                    "Attempt %d/%d failed for %s", attempt, max_attempts, file_path
                )
                if attempt >= max_attempts:
                    LOGGER.error("Posting failed after %d attempt(s).", max_attempts)
                    try:
                        alert_func(
                            "[Instagram AI Image] Posting Failed",
                            (
                                f"The following error occurred after {max_attempts} "
                                f"attempts: {exc}\nFile Path: {file_path}"
                            ),
                        )
                    except Exception:
                        LOGGER.exception("Failed to send posting-failure email alert.")
                    return 1

                delay = retry_delay * (2 ** (attempt - 1))
                LOGGER.info("Retrying in %.2f seconds.", delay)
                sleep_func(delay)

        return 1
    except (
        ConfigurationError,
        QuoteValidationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        TypeError,
    ):
        LOGGER.exception("Upload process failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
