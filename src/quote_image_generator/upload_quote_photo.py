#!/usr/bin/env python3
"""Publish one completed, receipt-backed artefact without replaying uncertainty."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from quote_image_generator.pipeline import main as workflow_main

    return workflow_main(["--stage", "publish"])


if __name__ == "__main__":
    raise SystemExit(main())
