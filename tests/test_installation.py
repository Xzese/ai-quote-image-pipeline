"""Installed imports and repository assets must work outside the checkout cwd."""

import json
import os
from pathlib import Path
import subprocess
import sys

from quote_image_generator import config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_installed_package_and_publisher_import_from_another_directory(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from pathlib import Path; "
            "from quote_image_generator import config; "
            "from upload_photo import upload_photo; "
            "print(json.dumps({'root': str(config.REPO_ROOT), "
            "'package': str(Path(config.__file__).resolve().parent), "
            "'publisher': str(Path(upload_photo.__file__).resolve())}))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = json.loads(result.stdout)
    assert paths == {
        "root": str(REPO_ROOT),
        "package": str(REPO_ROOT / "src" / "quote_image_generator"),
        "publisher": str(REPO_ROOT / "upload_photo" / "upload_photo.py"),
    }


def test_default_env_and_relative_assets_resolve_to_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    captured = []
    monkeypatch.setattr(config.dotenv, "load_dotenv", lambda **kw: captured.append(kw))
    config.load_project_env()
    assert captured == [{"dotenv_path": REPO_ROOT / ".env"}]
    for path in (
        "assets/fonts/Alegreya-VariableFont.ttf",
        "workflows/image_sd15.json",
        "fixtures/evaluation.json",
    ):
        assert config.resolve_repo_path(path) == REPO_ROOT / path
        assert config.resolve_repo_path(path).is_file()


def test_installed_pipeline_offline_command_from_another_directory(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    output = tmp_path / "demo"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quote_image_generator.pipeline",
            "--offline",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["exit_code"] == 0 and summary["eligible_renders"] == 6
    assert len(list(output.glob("*.jpeg"))) == 6
