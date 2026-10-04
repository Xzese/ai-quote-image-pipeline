"""Durable local receipts. All cooperating entry points share one corpus lock."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from filelock import FileLock, Timeout


class RunBusy(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        # Persist the rename as well as the contents on POSIX filesystems.
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def corpus_lock(corpus):
    path = Path(corpus).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path) + ".lock", timeout=0):
            yield
    except Timeout as exc:
        raise RunBusy("Another run owns this quote corpus.") from exc


class StateStore:
    def __init__(self, corpus):
        self.path = Path(str(Path(corpus).resolve()) + ".state.json")
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if self.data.get("version") != 1:
                raise ValueError("Unsupported workflow state version.")
        else:
            self.data = {"version": 1, "items": {}, "publications": {}}

    def save(self):
        atomic_json(self.path, self.data)

    def item(self, record):
        fingerprint = digest({k: record[k] for k in ("_id", "content", "author")})
        existing = self.data["items"].get(record["_id"])
        if not existing or existing.get("source") != fingerprint:
            existing = {"source": fingerprint}
            if record["_id"] in self.data["items"]:
                # Retain evidence of source edits even when receipts are invalidated.
                # A legacy importer must not adopt the old quote's image later.
                existing["legacy_import_blocked"] = True
            self.data["items"][record["_id"]] = existing
        return existing
