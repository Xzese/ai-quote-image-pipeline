#!/usr/bin/env python3
"""Publish one eligible render and report an explicit stage outcome."""

from __future__ import annotations

from dataclasses import asdict
from datetime import time as dt_time
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import random
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quote_image_generator.config import (
    get_env_int,
    get_env_str,
    load_project_env,
    resolve_repo_path,
)
from quote_image_generator.facebook_token_provider import (
    configure_facebook_token_from_provider,
)
from quote_image_generator.publishing_run import PublishingSummary, publish_one
from quote_image_generator.quote_validation import (
    safe_output_file_path,
    validate_quote_records,
)

LOGGER = logging.getLogger(__name__)


def _post_quote_photo(file_path: str, caption: str):
    from upload_photo.upload_photo import publish_image

    return publish_image(file_path, caption)


def _send_alert(subject, body):
    from upload_photo.upload_photo import send_email_alert

    return send_email_alert(subject, body)


def _configure_logging():
    if logging.getLogger().handlers:
        return
    level = get_env_str("UPLOAD_QUOTE_LOG_LEVEL", "INFO")
    retention = get_env_int("UPLOAD_QUOTE_LOG_RETENTION_WEEKS", 4)
    if retention is None or retention < 1:
        raise ValueError("UPLOAD_QUOTE_LOG_RETENTION_WEEKS must be positive.")
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logging.getLogger().addHandler(console)
    try:
        path = resolve_repo_path("output/logs/upload_quote_photo.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            path,
            when="W0",
            interval=1,
            backupCount=retention,
            atTime=dt_time(0, 0),
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
    except OSError:
        pass
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))


def _path_for(image_dir, quote_id):
    return safe_output_file_path(image_dir, quote_id, 1024, 1024, "jpeg")


def main(*, post_func=None, alert_func=None, sleep_func=None, randomizer=random):
    """sleep_func is retained for callers but never used: no post retries."""
    try:
        load_project_env()
        _configure_logging()
        quotes_file = get_env_str("QUOTES_FILE_PATH", "quotes.json", required=True)
        image_dir = get_env_str(
            "OVERLAY_OUTPUT_PATH", "output/images_text_overlay", required=True
        )
        with resolve_repo_path(quotes_file).open(encoding="utf-8") as source:
            records = validate_quote_records(json.load(source))
        summary = publish_one(
            records,
            resolve_repo_path(image_dir),
            path_for=_path_for,
            choose=randomizer.choice,
            post=post_func or _post_quote_photo,
            prepare=configure_facebook_token_from_provider,
            alert=alert_func or _send_alert,
        )
    except Exception:
        summary = PublishingSummary("failed")
    # Reporting failure cannot turn a confirmed post into another post attempt.
    try:
        report = json.dumps(asdict(summary), sort_keys=True)
        print(report)
        LOGGER.info("Publishing stage: %s", summary.status)
    except Exception:
        pass
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
