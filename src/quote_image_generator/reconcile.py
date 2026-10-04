"""Record an operator-verified publishing outcome without contacting Meta."""

import argparse
import json
from pathlib import Path

from quote_image_generator.pipeline import now
from quote_image_generator.run_state import RunBusy, StateStore, corpus_lock


def reconcile(corpus, operation_id, *, media_id=None, not_published=False):
    if bool(media_id) == bool(not_published):
        raise ValueError("Select exactly one verified outcome.")
    if media_id is not None and (not isinstance(media_id, str) or not media_id.strip()):
        raise ValueError("A confirmed media ID is required.")
    with corpus_lock(corpus):
        store = StateStore(corpus)
        operation = store.data["publications"][operation_id]
        if operation["status"] != "unknown":
            raise ValueError("Only an unknown operation may be reconciled.")
        operation["reconciled_at"] = now()
        if media_id:
            operation.update(status="published", media_id=media_id.strip())
        else:
            operation["status"] = "not_published"
        store.save()
        return dict(operation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("operation_id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--media-id", help="Media ID verified in the provider account.")
    group.add_argument(
        "--not-published",
        action="store_true",
        help="Explicitly confirm no publication occurred; permits another attempt.",
    )
    args = parser.parse_args(argv)
    try:
        result = reconcile(
            args.corpus,
            args.operation_id,
            media_id=args.media_id,
            not_published=args.not_published,
        )
    except RunBusy:
        print(json.dumps({"status": "busy"}))
        return 75
    except (ValueError, KeyError, OSError):
        print(
            json.dumps(
                {"status": "failed", "reason": "invalid_operation_or_persistence"}
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
