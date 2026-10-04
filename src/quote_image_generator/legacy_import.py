"""Adopt existing quote JPEGs once, without inventing generation provenance."""

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading

from PIL import Image

from quote_image_generator.config import (
    get_env_str,
    load_project_env,
    resolve_repo_path,
)
from quote_image_generator.quote_validation import (
    safe_output_file_path,
    validate_quote_records,
)
from quote_image_generator.run_state import StateStore, RunBusy, corpus_lock, digest

LEGACY_SIZE = (1024, 1024)
IMPORT_VERSION = 1


def _file_identity(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _image_hash(path, root):
    """Validate and hash the same open file; reject links outside the image root."""
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Legacy image is outside the configured image directory.")
    with path.open("rb") as source:
        # Opening once prevents validation and hashing different file contents.
        before = os.fstat(source.fileno())
        with Image.open(source) as image:
            if (
                image.format != "JPEG"
                or image.size != LEGACY_SIZE
                or image.mode != "RGB"
            ):
                raise ValueError("Legacy image must be an RGB 1024x1024 JPEG.")
            image.load()
        source.seek(0)
        sha = hashlib.file_digest(source, "sha256").hexdigest()
        after = os.fstat(source.fileno())
    current = path.stat()
    if _file_identity(before) != _file_identity(after) or _file_identity(
        after
    ) != _file_identity(current):
        raise ValueError("Legacy image changed during validation.")
    return path, sha


def import_legacy_images(records, image_dir, store, *, dry_run=False, stop_event=None):
    """Caller owns corpus/image locks. Commit a complete migration atomically.

    Existing receipts and publication claims are never replaced. The migration
    marker prevents adopting a stale file after a later quote or caption edit.
    Imported provenance explicitly leaves the original model/workflow unknown.
    """
    from quote_image_generator.pipeline import prompt_input, prompt_output, render_input

    previous = store.data.get("legacy_import")
    if previous is not None:
        if not isinstance(previous, dict) or previous.get("version") != IMPORT_VERSION:
            raise ValueError("Unsupported legacy import marker.")
        return {"status": "already_completed", "imported": 0, "excluded": len(records)}
    root = Path(image_dir).resolve()
    stop_event = stop_event or threading.Event()
    imported_at = datetime.now(timezone.utc).isoformat()
    updates = {}
    reasons = Counter()
    for record in records:
        if stop_event.is_set():
            return {"status": "cancelled", "imported": 0, "excluded": len(records)}
        source = digest({k: record[k] for k in ("_id", "content", "author")})
        existing = store.data["items"].get(record["_id"])
        # A previous publish-only run may have created a bare source entry.
        if existing is not None and existing != {"source": source}:
            reasons["existing_state"] += 1
            continue
        if not all(
            isinstance(record.get(k), str) and record[k].strip()
            for k in ("prompt", "hashtags")
        ):
            reasons["missing_prompt_or_hashtags"] += 1
            continue
        try:
            path = safe_output_file_path(root, record["_id"], *LEGACY_SIZE, "jpeg")
            path, sha = _image_hash(path, root)
        except (OSError, ValueError, Image.DecompressionBombError):
            reasons["missing_or_invalid_image"] += 1
            continue
        metadata = {
            "origin": "legacy-import",
            "original_generation_metadata": "unknown",
        }
        item = {"source": source}
        item["prompt"] = {
            "status": "completed",
            "input": prompt_input(record, metadata),
            "output": prompt_output(record),
            "metadata": dict(metadata),
            "imported_at": imported_at,
        }
        item["render"] = {
            "status": "completed",
            "input": render_input(record, item, metadata),
            "metadata": dict(metadata),
            "path": str(path),
            "sha256": sha,
            "imported_at": imported_at,
        }
        updates[record["_id"]] = item
    if stop_event.is_set():
        return {"status": "cancelled", "imported": 0, "excluded": len(records)}
    summary = {
        "status": "preview" if dry_run else "completed" if updates else "skipped",
        "imported": len(updates),
        "excluded": len(records) - len(updates),
        "reasons": dict(reasons),
    }
    if not dry_run:
        previous_data = store.data
        store.data = {
            **previous_data,
            "items": {**previous_data["items"], **updates},
            "legacy_import": {
                "version": IMPORT_VERSION,
                "completed_at": imported_at,
                **summary,
            },
        }
        try:
            store.save()
        except BaseException:
            store.data = previous_data
            raise
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, help="Default: QUOTES_FILE_PATH.")
    parser.add_argument("--image-dir", type=Path, help="Default: OVERLAY_OUTPUT_PATH.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without changing receipts or importing images.",
    )
    args = parser.parse_args(argv)
    load_project_env()
    try:
        corpus = resolve_repo_path(
            args.corpus or get_env_str("QUOTES_FILE_PATH", "output/quotes.json")
        )
        image_dir = resolve_repo_path(
            args.image_dir
            or get_env_str("OVERLAY_OUTPUT_PATH", "output/images_text_overlay")
        )
        with ExitStack() as locks:
            locks.enter_context(corpus_lock(corpus))
            locks.enter_context(corpus_lock(image_dir / ".pipeline"))
            records = validate_quote_records(
                json.loads(corpus.read_text(encoding="utf-8"))
            )
            result = import_legacy_images(
                records, image_dir, StateStore(corpus), dry_run=args.dry_run
            )
        exit_code = 0
    except RunBusy:
        result, exit_code = {"status": "busy"}, 75
    except (OSError, ValueError, TypeError):
        result, exit_code = (
            {"status": "failed", "reason": "configuration_or_persistence_failure"},
            1,
        )
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
