"""A single publishing stage with explicit outcomes and no implicit retries."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublishingSummary:
    status: str
    quote_id: str | None = None
    media_id: str | None = None
    excluded: int = 0

    @property
    def exit_code(self):
        return 0 if self.status in {"published", "skipped"} else 1


def publish_one(records, image_dir, *, path_for, choose, post, prepare, alert):
    """Select existing, non-empty renders. A post failure is never replayed.

    This guards one process invocation only. Persistent operation ownership and
    reconciliation across repeated scheduler runs remain separate work.
    """
    eligible = []
    root = Path(image_dir).resolve()
    for record in records:
        path = path_for(root, record["_id"])
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            eligible.append((record, resolved))
    excluded = len(records) - len(eligible)
    if not eligible:
        return PublishingSummary("skipped", excluded=excluded)
    selected = choose(eligible)
    if selected not in eligible:
        raise ValueError("Selection is not an eligible render.")
    record, path = selected
    try:
        prepare()
    except Exception:
        return PublishingSummary("failed", record["_id"], excluded=excluded)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return PublishingSummary("failed", record["_id"], excluded=excluded)
        result = post(str(path), record.get("hashtags", ""))
        media_id = (
            result.get("id")
            if isinstance(result, dict)
            else getattr(result, "media_id", None)
        )
        if isinstance(media_id, str) and media_id.strip():
            return PublishingSummary("published", record["_id"], media_id, excluded)
    except Exception:
        pass
    # An exception or missing confirmation does not establish remote failure.
    try:
        alert(
            "[Instagram AI Image] Outcome Unconfirmed",
            "Publication was not confirmed. Inspect the account and existing "
            "operation before another run. No automatic retry was made.",
        )
    except Exception:
        pass
    return PublishingSummary("unknown", record["_id"], excluded=excluded)
