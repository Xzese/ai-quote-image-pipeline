from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Sequence

_FILENAME_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f<>:\"/\\\\|?*]")
_WINDOWS_RESERVED_BASENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


class QuoteValidationError(ValueError):
    """Raised when quote payload entries do not match the expected schema."""


def _require_str(record: Mapping[str, object], field: str, index: int) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r}; expected string, got {type(value).__name__}"
        )
    value = value.strip()
    if not value:
        raise QuoteValidationError(f"Record {index} has empty {field!r}.")
    return value


def _ensure_no_path_separators(value: str, field: str, index: int) -> None:
    if re.search(r"[\\\\/]", value):
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r} {value!r}: path separators are not allowed."
        )


def _validate_output_safe_identifier(value: str, field: str, index: int) -> str:
    if not value:
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r}: empty value."
        )

    if value in {".", ".."}:
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r} {value!r}: '.' and '..' are not allowed."
        )

    if value.endswith((".", " ")):
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r} {value!r}: trailing dot or space is not allowed."
        )

    if _FILENAME_CONTROL_CHARS_RE.search(value):
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r} {value!r}: contains unsupported filename character(s)."
        )

    if value.rstrip(" .").upper() in _WINDOWS_RESERVED_BASENAMES:
        raise QuoteValidationError(
            f"Record {index} has invalid {field!r} {value!r}: name is reserved on Windows."
        )

    return value


def validate_quote_record(record: object, index: int) -> dict[str, object]:
    if not isinstance(record, Mapping):
        raise QuoteValidationError(
            f"Record {index} is not an object; got {type(record).__name__}"
        )

    record_dict = dict(record)
    quote_id = _require_str(record_dict, "_id", index)
    _ensure_no_path_separators(quote_id, "_id", index)
    content = _require_str(record_dict, "content", index)
    author = _require_str(record_dict, "author", index)

    for optional_field in ("prompt", "hashtags"):
        if optional_field in record_dict and not isinstance(
            record_dict[optional_field], str
        ):
            raise QuoteValidationError(
                f"Record {index} has invalid {optional_field!r}; expected string if present."
            )

    record_dict["_id"] = quote_id
    record_dict["content"] = content
    record_dict["author"] = author
    return record_dict


def validate_quote_records(records: object) -> list[dict[str, object]]:
    if not isinstance(records, Sequence):
        raise QuoteValidationError(
            f"Expected a list of quote records, got {type(records).__name__}"
        )

    validated_records: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        validated = validate_quote_record(record, index)
        quote_id = validated["_id"]
        assert isinstance(quote_id, str)
        if quote_id in seen_ids:
            raise QuoteValidationError(f"Duplicate _id {quote_id!r} at record {index}.")
        seen_ids.add(quote_id)
        validated_records.append(validated)

    return validated_records


def safe_output_filename(
    quote_id: str,
    width: int,
    height: int,
    extension: str,
) -> str:
    _ensure_no_path_separators(quote_id, "_id", 0)
    _validate_output_safe_identifier(quote_id, "_id", 0)
    if not extension:
        raise QuoteValidationError("Output filename extension must be provided.")

    normalized_extension = extension.lstrip(".")
    if not normalized_extension:
        raise QuoteValidationError("Output filename extension cannot be empty.")

    if width <= 0 or height <= 0:
        raise QuoteValidationError("Output image dimensions must be positive integers.")

    return f"{quote_id}{width}x{height}.{normalized_extension}"


def safe_output_file_path(
    output_dir: Path | str,
    quote_id: str,
    width: int,
    height: int,
    extension: str,
) -> Path:
    output_name = safe_output_filename(quote_id, width, height, extension)
    return Path(output_dir) / output_name
