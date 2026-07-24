import pytest

from quote_image_generator.quote_validation import (
    QuoteValidationError as ValidationError,
    safe_output_file_path,
    safe_output_filename,
    validate_quote_record,
    validate_quote_records,
)


def test_validate_quote_records_rejects_noniterable_input():
    with pytest.raises(ValidationError, match="Expected a list of quote records"):
        validate_quote_records({"_id": "1"})


def test_validate_quote_records_rejects_duplicate_ids():
    data = [
        {"_id": "dup", "content": "first", "author": "one"},
        {"_id": "dup", "content": "second", "author": "two"},
    ]

    with pytest.raises(ValidationError, match="Duplicate _id 'dup'"):
        validate_quote_records(data)


def test_validate_quote_record_rejects_nonstring_fields():
    record = {"_id": 123, "content": 456, "author": None}

    with pytest.raises(ValidationError, match="invalid '_id'; expected string"):
        validate_quote_record(record, index=0)


def test_validate_quote_record_rejects_path_separator_in_id():
    record = {"_id": "../bad/id", "content": "ok", "author": "me"}

    with pytest.raises(ValidationError, match="path separators are not allowed"):
        validate_quote_record(record, index=0)


def test_validate_quote_record_rejects_nonstring_optional_fields():
    record = {
        "_id": "ok-id",
        "content": "Hello world",
        "author": "Me",
        "prompt": ["no", "list"],
        "hashtags": 123,
    }

    with pytest.raises(
        ValidationError, match="invalid 'prompt'; expected string if present"
    ):
        validate_quote_record(record, index=0)


def test_safe_output_filename_generates_expected_name():
    assert safe_output_filename("my-id", 1024, 1024, "png") == "my-id1024x1024.png"
    assert safe_output_filename("my-id", 1024, 1024, ".png") == "my-id1024x1024.png"


def test_safe_output_filename_rejects_invalid_inputs():
    with pytest.raises(
        ValidationError, match="Output filename extension must be provided"
    ):
        safe_output_filename("id", 1, 1, "")

    with pytest.raises(ValidationError, match="dimensions must be positive"):
        safe_output_filename("id", 0, 1024, "png")

    with pytest.raises(ValidationError, match="dimensions must be positive"):
        safe_output_filename("id", 1024, -1, "png")

    with pytest.raises(ValidationError, match="path separators are not allowed"):
        safe_output_filename("bad/id", 1, 1, "png")

    with pytest.raises(
        ValidationError,
        match="reserved on Windows|windows device",
    ):
        safe_output_filename("CON", 1, 1, "png")


def test_safe_output_filename_rejects_windows_reserved_id():
    with pytest.raises(ValidationError, match="reserved on Windows"):
        safe_output_filename("LPT1", 1, 1, "png")


def test_safe_output_filename_rejects_dot_names():
    with pytest.raises(
        ValidationError,
        match="'\\. and '\\.\\.' are not allowed|\\'\\.\\' and '\\.\\.' are not allowed",
    ):
        safe_output_filename("..", 1, 1, "png")


def test_safe_output_filename_rejects_invalid_control_characters():
    with pytest.raises(ValidationError, match="unsupported filename character"):
        safe_output_filename("bad\x00id", 1, 1, "png")


def test_safe_output_filename_rejects_disallowed_characters():
    with pytest.raises(ValidationError, match="unsupported filename character"):
        safe_output_filename('bad"id', 1, 1, "png")


def test_safe_output_file_path_joins_output_directory(tmp_path):
    output_dir = tmp_path / "out"
    path = safe_output_file_path(output_dir, "img", 16, 16, "jpg")

    assert path == output_dir / "img16x16.jpg"


def test_validate_quote_records_accepts_optional_prompt_and_hashtags():
    records = [
        {
            "_id": "id-1",
            "content": "Hello",
            "author": "Anonymous",
            "prompt": "existing prompt",
            "hashtags": "#calm #focus",
        }
    ]

    validated = validate_quote_records(records)

    assert validated == records
    assert all(
        isinstance(value, str)
        for value in [validated[0]["prompt"], validated[0]["hashtags"]]
    )
