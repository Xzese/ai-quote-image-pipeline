# AI pipeline maturity pass

The implementation now covers the planned workflow semantics and reproducibility
work. Small-model LM Studio, Ollama and ComfyUI generation smoke tests passed.
Keep the PR in draft until the intended production-model image-quality review
and optional object-storage/Meta smoke tests have been completed.

- Explicit prompt, render and publishing outcomes, partial-success reporting and exit codes.
- Completed-artefact receipts, cache invalidation and publication eligibility.
- Cross-process corpus, output-directory and local LM Studio coordination.
- Durable publication claims, container/media IDs and operator reconciliation.
- Model, template, workflow, seed, generation and artefact metadata.
- A documented local word limit, replacing the unrelated BERT token count.
- A fixed authored evaluation corpus, measurable summaries and human review guidance.
- A deterministic offline demo using the real overlay and persistence code.
- One overall ComfyUI submission/poll/download deadline, including slow response bodies.
- Hardened publisher integration, readiness polling and redacted notifications.
- Updated hashed dependency pins with the unused vulnerable tokenizer dependency removed.
- Selectable Ollama native structured chat and a small SD 1.5 image workflow.
- Default fetch → all prompts → owned-model release → all renders, with an
  optional per-quote order and cache-preserving source refreshes.
- Application code in `src/quote_image_generator/`, tests in `tests/`, and
  editable project installation through `pyproject.toml`.

See [workflow documentation](../docs/workflow.md) for the contracts, migration
instructions, recovery commands and validation boundaries. CI retains both
projects' tests, formatting, lint, compilation and vulnerability audits, and now
executes the offline demo.
