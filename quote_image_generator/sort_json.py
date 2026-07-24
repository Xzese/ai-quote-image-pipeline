from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import Counter
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quote_image_generator.config import get_env_str, load_project_env, resolve_repo_path
from quote_image_generator.quote_validation import QuoteValidationError


def load_quote_file(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as json_file:
        data = json.load(json_file)

    if not isinstance(data, list):
        raise QuoteValidationError(
            "The JSON file does not contain an array of objects."
        )

    return data


def print_post_count_summary(quote_data: list[dict[str, Any]]) -> None:
    post_counts = [
        int(item.get("post_count", 0)) if item.get("post_count") else 0
        for item in quote_data
    ]
    post_count_counts = Counter(post_counts)
    sorted_counts = sorted(post_count_counts.items(), key=lambda x: x[0], reverse=True)
    print("Counts of post_count values (sorted by post_count descending):")
    for post_count, count in sorted_counts:
        print(f"Post Count: {post_count}, Count: {count}")


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Summarize quote post_count frequencies."
    )
    parser.add_argument(
        "quotes_file_path",
        nargs="?",
        help="Path to the quotes JSON file.",
    )
    args = parser.parse_args(argv)

    json_path = args.quotes_file_path or get_env_str("QUOTES_FILE_PATH")
    if not json_path:
        print(
            "Missing input path. Provide a positional path argument or set QUOTES_FILE_PATH.",
            file=sys.stderr,
        )
        return 2

    resolved_path = resolve_repo_path(json_path)
    if not resolved_path.exists():
        print(f"File not found: {resolved_path}", file=sys.stderr)
        return 2

    if not resolved_path.is_file():
        print(f"Invalid input path (not a file): {resolved_path}", file=sys.stderr)
        return 2

    try:
        quote_data = load_quote_file(resolved_path)
        print_post_count_summary(quote_data)
    except QuoteValidationError as exc:
        print(f"Invalid data: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Failed to parse JSON: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Unable to read file: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
