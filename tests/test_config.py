import os

from quote_image_generator import config
import pytest


def test_load_project_env_reads_dotenv_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEMP_TEST_ENV=loaded_value\n")

    monkeypatch.delenv("TEMP_TEST_ENV", raising=False)
    config.load_project_env(dotenv_path=env_file)

    assert os.getenv("TEMP_TEST_ENV") == "loaded_value"


def test_resolve_repo_path_keeps_absolute_paths(tmp_path):
    absolute = tmp_path / "absolute.txt"

    result = config.resolve_repo_path(absolute)

    assert result == absolute


def test_resolve_repo_path_resolves_relative_to_repo_root():
    result = config.resolve_repo_path("data/output")

    assert result == config.REPO_ROOT / "data" / "output"


def test_get_env_str_handles_required_and_stripping(monkeypatch):
    monkeypatch.setenv("TEMP_TEST_ENV", "  spaced_value  ")

    assert config.get_env_str("TEMP_TEST_ENV") == "spaced_value"
    assert config.get_env_str("TEMP_TEST_ENV", required=True) == "spaced_value"

    with pytest.raises(config.ConfigurationError, match="TEMP_MISSING is required"):
        config.get_env_str("TEMP_MISSING", required=True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42", 42),
        ("  100  ", 100),
        ("-1", -1),
        (None, None),
    ],
)
def test_get_env_int_parses_ints(value, expected, monkeypatch):
    if value is None:
        monkeypatch.delenv("TEMP_INT_ENV", raising=False)
    else:
        monkeypatch.setenv("TEMP_INT_ENV", value)

    if value is None:
        assert config.get_env_int("TEMP_INT_ENV", default=None) is None
    else:
        assert config.get_env_int("TEMP_INT_ENV") == expected


def test_get_env_int_invalid_value(monkeypatch):
    monkeypatch.setenv("TEMP_INT_ENV", "not-an-int")

    with pytest.raises(config.ConfigurationError, match="must be an integer"):
        config.get_env_int("TEMP_INT_ENV")


def test_get_env_bool_parses_known_values(monkeypatch):
    bool_cases = {
        "1": True,
        "true": True,
        "YES": True,
        "on": True,
        "0": False,
        "false": False,
        "No": False,
        "OFF": False,
    }

    for raw, expected in bool_cases.items():
        monkeypatch.setenv("TEMP_BOOL_ENV", raw)
        assert config.get_env_bool("TEMP_BOOL_ENV") is expected


def test_get_env_bool_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("TEMP_BOOL_ENV", "maybe")

    with pytest.raises(config.ConfigurationError, match="must be a boolean-like value"):
        config.get_env_bool("TEMP_BOOL_ENV")


def test_get_required_file_path_creates_parent_directory(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "quotes.json"
    monkeypatch.setenv("QUOTES_FILE_PATH", str(target))

    result = config.get_required_file_path("QUOTES_FILE_PATH")

    assert result == target
    assert target.parent.exists()
    assert target.parent.is_dir()


def test_get_required_file_path_missing_key_raises():
    if "TEMP_MISSING_PATH" in os.environ:
        del os.environ["TEMP_MISSING_PATH"]

    with pytest.raises(
        config.ConfigurationError, match="TEMP_MISSING_PATH is required"
    ):
        config.get_required_file_path("TEMP_MISSING_PATH")


def test_ensure_directory_creates_path(tmp_path):
    folder = tmp_path / "one" / "two"

    created = config.ensure_directory(folder)

    assert created == folder
    assert folder.exists()
    assert folder.is_dir()
