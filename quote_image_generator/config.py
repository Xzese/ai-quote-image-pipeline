from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

import dotenv

T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


def load_project_env(dotenv_path: os.PathLike[str] | str | None = None) -> None:
    env_path = Path(dotenv_path) if dotenv_path is not None else (REPO_ROOT / ".env")
    dotenv.load_dotenv(dotenv_path=env_path)


def resolve_repo_path(path_value: os.PathLike[str] | str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def _coerce_env_value(
    name: str,
    value: object,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(
            f"Environment value for {name} must be a string, got {type(value).__name__}"
        )
    trimmed = value.strip()
    return trimmed if trimmed else None


def get_env_str(
    name: str, default: str | None = None, required: bool = False
) -> str | None:
    value = _coerce_env_value(name, os.getenv(name, default))
    if value is None and required:
        raise ConfigurationError(f"{name} is required.")
    return value


def get_env_int(
    name: str,
    default: int | None = None,
    required: bool = False,
) -> int | None:
    value = get_env_str(
        name, default=None if default is None else str(default), required=required
    )
    if value is None:
        return None

    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {value!r}.") from exc


def get_env_bool(
    name: str, default: bool | None = None, required: bool = False
) -> bool | None:
    value = get_env_str(
        name, default=None if default is None else str(default), required=required
    )
    if value is None:
        return None

    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean-like value, got {value!r}.")


def get_required_file_path(
    name: str,
    *,
    create_parent: bool = True,
) -> Path:
    path_value = get_env_str(name, required=True)
    assert path_value is not None
    path = resolve_repo_path(path_value)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_directory(path: os.PathLike[str] | str) -> Path:
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
