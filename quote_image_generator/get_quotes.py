from __future__ import annotations

import json
import sys
from typing import Any

import requests

from .config import (
    ConfigurationError,
    get_env_str,
    get_required_file_path,
    load_project_env,
)
from .quote_validation import validate_quote_records

DEFAULT_ENDPOINT_URL = "https://api.quotable.io/quotes"
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
) -> list[dict[str, Any]]:
    if page_limit <= 0:
        raise ValueError("page_limit must be greater than zero.")

    current_page = 1
    total_pages = 1
    quotes: list[dict[str, Any]] = []

    while current_page <= total_pages:
        print(f"Page Number: {current_page}")
        results, total_pages = fetch_quotable_page(
            session=session,
            endpoint_url=endpoint_url,
            page=current_page,
            limit=page_limit,
            timeout_seconds=timeout_seconds,
        )
        quotes.extend(results)
        current_page += 1

    return quotes


def write_quotes(path, quotes: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as json_file:
        json.dump(quotes, json_file, indent=4)


def main() -> int:
    load_project_env()

    try:
        quotes_file_path = get_required_file_path("QUOTES_FILE_PATH")
        endpoint_url = get_env_str("QUOTES_ENDPOINT_URL") or DEFAULT_ENDPOINT_URL

        with requests.Session() as session:
            quote_list = fetch_quotes(session=session, endpoint_url=endpoint_url)

        validated_quotes = validate_quote_records(quote_list)
        write_quotes(quotes_file_path, validated_quotes)
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
