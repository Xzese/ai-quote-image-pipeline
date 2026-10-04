"""Durable single-attempt publishing with explicit outcomes and no replay."""

from dataclasses import dataclass
import os
from pathlib import Path

from quote_image_generator.run_state import digest, file_digest


@dataclass(frozen=True)
class PublishingSummary:
    status: str
    quote_id: str | None = None
    media_id: str | None = None
    excluded: int = 0
    operation_id: str | None = None
    creation_id: str | None = None

    @property
    def exit_code(self):
        return 0 if self.status in {"published", "skipped"} else 1


def publish_one(records, image_dir, *, path_for, choose, post, prepare, alert, store):
    """Caller must hold the corpus lock. An unknown claim survives process death.

    No legacy file-only eligibility: prompt and render receipts must still match.
    Journal identity includes account and source, so re-rendering cannot replay a
    quote already posted to that account. Operators reconcile unknown claims.
    """
    from quote_image_generator.pipeline import (
        completed_render,
        prompt_output,
        render_input,
    )

    eligible = []
    account = os.getenv("IG_BUSINESS_USER_ID", "").strip()
    for key, operation in store.data["publications"].items():
        if operation.get("account") == account and operation.get("status") == "unknown":
            return PublishingSummary(
                "unknown",
                operation.get("quote_id"),
                excluded=len(records),
                operation_id=key,
                creation_id=operation.get("creation_id"),
            )
    for record in records:
        state = store.item(record)
        receipt = state.get("render", {})
        prompt = state.get("prompt", {})
        if prompt.get("status") != "completed" or prompt.get("output") != prompt_output(
            record
        ):
            continue
        if not completed_render(
            receipt, render_input(record, state, receipt.get("metadata", {}))
        ):
            continue
        path = Path(receipt["path"]).resolve()
        if image_dir is not None and not path.is_relative_to(Path(image_dir).resolve()):
            continue
        key = digest(
            {"account": account, "quote_id": record["_id"], "source": state["source"]}
        )
        previous = store.data["publications"].get(key)
        if previous and previous["status"] != "not_published":
            if previous["status"] != "published":
                # Block further publications for this account until reconciliation.
                return PublishingSummary(
                    "unknown",
                    record["_id"],
                    excluded=len(records),
                    operation_id=key,
                    creation_id=previous.get("creation_id"),
                )
            continue
        eligible.append((record, path, key, receipt["sha256"]))
    excluded = len(records) - len(eligible)
    if not eligible:
        return PublishingSummary("skipped", excluded=excluded)
    selected = choose(eligible)
    if selected not in eligible:
        raise ValueError("Selection is not an eligible render.")
    record, path, key, sha = selected
    try:
        prepare()
        if file_digest(path) != sha:
            raise ValueError("Artefact changed before publication.")
    except Exception:
        return PublishingSummary("failed", record["_id"], excluded=excluded)
    journal = {
        "status": "unknown",
        "quote_id": record["_id"],
        "sha256": sha,
        "account": account,
    }
    previous = store.data["publications"].get(key)
    if previous:
        journal["prior_attempts"] = previous.get("prior_attempts", []) + [
            {k: v for k, v in previous.items() if k != "prior_attempts"}
        ]
    store.data["publications"][key] = journal
    # Persist before invoking any external effect. Persistence failure stops here.
    store.save()

    def checkpoint(creation_id):
        if not isinstance(creation_id, str) or not creation_id.strip():
            raise ValueError("A valid container ID is required.")
        journal["creation_id"] = creation_id
        store.save()

    try:
        # The production adapter supports a checkpoint before media_publish.
        if getattr(post, "supports_checkpoint", False) is True:
            result = post(str(path), record.get("hashtags", ""), checkpoint=checkpoint)
        else:
            result = post(str(path), record.get("hashtags", ""))
        media_id = (
            result.get("id")
            if isinstance(result, dict)
            else getattr(result, "media_id", None)
        )
        creation_id = (
            result.get("creation_id")
            if isinstance(result, dict)
            else getattr(result, "creation_id", None)
        )
        if isinstance(creation_id, str):
            journal["creation_id"] = creation_id
        if isinstance(media_id, str) and media_id.strip():
            journal.update(status="published", media_id=media_id)
            store.save()
            return PublishingSummary(
                "published",
                record["_id"],
                media_id,
                excluded,
                key,
                journal.get("creation_id"),
            )
    except Exception as exc:
        creation_id = getattr(exc, "creation_id", None)
        if isinstance(creation_id, str):
            journal["creation_id"] = creation_id
        # If confirmation persistence failed, keep memory and durable state unknown.
        journal["status"] = "unknown"
    try:
        store.save()
    except Exception:
        pass
    try:
        alert(
            "[Instagram AI Image] Outcome Unconfirmed",
            "Publication was not confirmed. Reconcile the durable operation before another run. No automatic retry was made.",
        )
    except Exception:
        pass
    return PublishingSummary(
        "unknown",
        record["_id"],
        excluded=excluded,
        operation_id=key,
        creation_id=journal.get("creation_id"),
    )
