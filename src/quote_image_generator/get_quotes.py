from __future__ import annotations

from pathlib import Path
import sys
import json
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quote_image_generator.config import (
    ConfigurationError,
    get_env_str,
    get_required_file_path,
    load_project_env,
)
from quote_image_generator.quote_validation import validate_quote_records
from quote_image_generator.run_state import atomic_json, corpus_lock, RunBusy

DEFAULT_ENDPOINT_URL = "http://api.quotable.io/quotes"
DEFAULT_PAGE_LIMIT = 150
DEFAULT_TIMEOUT_SECONDS = 30


def fetch_quotable_page(
    session: requests.Session,
    endpoint_url: str,
    page: int,
    limit: int,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], int]:
    response = session.get(
        endpoint_url,
        params={"limit": limit, "page": page},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected API response shape: expected a JSON object.")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Unexpected API response shape: 'results' is not a list.")

    total_pages = payload.get("totalPages")
    if not isinstance(total_pages, int):
        raise ValueError(
            "Unexpected API response shape: 'totalPages' is not an integer."
        )

    return results, total_pages


def fetch_quotes(
    session: requests.Session,
    endpoint_url: str = DEFAULT_ENDPOINT_URL,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stop_event=None,
) -> list[dict[str, Any]]:
    if page_limit <= 0:
        raise ValueError("page_limit must be greater than zero.")

    current_page = 1
    total_pages = 1
    quotes: list[dict[str, Any]] = []

    while current_page <= total_pages:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Quote retrieval cancelled.")
        print(f"Page Number: {current_page}", file=sys.stderr)
        results, total_pages = fetch_quotable_page(
            session=session,
            endpoint_url=endpoint_url,
            page=current_page,
            limit=page_limit,
            timeout_seconds=timeout_seconds,
        )
        quotes.extend(results)
        current_page += 1

    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Quote retrieval cancelled.")
    return quotes


def write_quotes(path, quotes: list[dict[str, Any]]) -> None:
    with corpus_lock(path):
        previous = (
            json.loads(Path(path).read_text(encoding="utf-8"))
            if Path(path).exists()
            else []
        )
        atomic_json(path, merge_quotes(previous, quotes))


def merge_quotes(previous, fetched):
    """Update known quotes and append new ones without deleting absent records."""
    existing = {r["_id"]: r for r in validate_quote_records(previous)}
    for record in validate_quote_records(fetched):
        record = {k: v for k, v in record.items() if k not in ("prompt", "hashtags")}
        old = existing.get(record["_id"], {})
        if all(old.get(k) == record[k] for k in ("_id", "content", "author")):
            record.update({k: old[k] for k in ("prompt", "hashtags") if k in old})
        existing[record["_id"]] = record
    return list(existing.values())


def retrieve_quotes(stop_event=None):
    endpoint = get_env_str("QUOTES_ENDPOINT_URL") or DEFAULT_ENDPOINT_URL
    with requests.Session() as session:
        quotes = fetch_quotes(
            session=session, endpoint_url=endpoint, stop_event=stop_event
        )
    quotes, duplicates = deduplicate_quotes_by_id(quotes)
    if duplicates:
        print(
            f"Warning: Removed {duplicates} duplicate quote record(s) before validation.",
            file=sys.stderr,
        )
    return validate_quote_records(quotes)


def deduplicate_quotes_by_id(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    deduplicated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicate_count = 0

    for record in records:
        quote_id = record.get("_id") if isinstance(record, dict) else None
        if isinstance(quote_id, str) and quote_id in seen_ids:
            duplicate_count += 1
            continue

        if isinstance(quote_id, str):
            seen_ids.add(quote_id)

        deduplicated.append(record)

    return deduplicated, duplicate_count


def main() -> int:
    load_project_env()

    try:
        quotes_file_path = get_required_file_path("QUOTES_FILE_PATH")
        write_quotes(quotes_file_path, retrieve_quotes())
    except RunBusy:
        print("Another run owns this quote corpus.", file=sys.stderr)
        return 75
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Invalid data: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Failed to retrieve data: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
