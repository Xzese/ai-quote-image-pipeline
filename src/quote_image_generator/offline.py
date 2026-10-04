"""A fixed, authored corpus and procedurally generated image fixtures."""

import json
from pathlib import Path

from PIL import Image

from quote_image_generator.config import resolve_repo_path
from quote_image_generator.get_image import FONT_FILE, overlay_text_on_image
from quote_image_generator.pipeline import PROMPT_TEMPLATE_VERSION, run_workflow
from quote_image_generator.run_state import (
    atomic_json,
    corpus_lock,
    digest,
    file_digest,
)


def demo(output, mode="batch"):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixtures = json.loads(
        resolve_repo_path("fixtures/evaluation.json").read_text(encoding="utf-8")
    )
    corpus = output / "corpus.json"
    with corpus_lock(corpus):
        if not corpus.exists():
            atomic_json(
                corpus,
                [{k: r[k] for k in ("_id", "content", "author")} for r in fixtures],
            )
        elif digest(
            [
                {k: r[k] for k in ("_id", "content", "author")}
                for r in json.loads(corpus.read_text())
            ]
        ) != digest(
            [{k: r[k] for k in ("_id", "content", "author")} for r in fixtures]
        ):
            raise ValueError("Demo destination contains a different corpus.")
    by_id = {r["_id"]: r for r in fixtures}

    def prompt(record):
        return {k: by_id[record["_id"]][k] for k in ("prompt", "hashtags")}

    def render(record):
        # This is a local image fixture, not an assessment of diffusion quality.
        index = next(i for i, r in enumerate(fixtures) if r["_id"] == record["_id"])
        base = output / (record["_id"] + ".png")
        final = output / (record["_id"] + ".jpeg")
        image = Image.new(
            "RGB", (1024, 1024), (30 + index * 15, 70 + index * 10, 110 + index * 8)
        )
        image.save(base)
        image.close()
        overlay_text_on_image(base, final, record["content"], record["author"])
        return {"path": str(final), "seed": 42, "fixture": "solid-background-v1"}

    return run_workflow(
        corpus,
        mode=mode,
        prompt=prompt,
        render=render,
        prompt_metadata={
            "model": "offline-fixture",
            "template_version": PROMPT_TEMPLATE_VERSION,
            "fixture_sha256": digest(fixtures),
            "measurement": "whitespace-words-v1",
        },
        resource_dirs=(output,),
        render_metadata={
            "fixture": "solid-background-v1",
            "seed": 42,
            "font_sha256": file_digest(FONT_FILE),
        },
    )
