# AI pipeline maturity pass

Placeholder for the next engineering pass on the existing local-first AI image pipeline.

## Scope
- Add explicit per-stage outcomes such as pending, running, completed, failed and skipped.
- Define partial-success semantics and make the final process result reflect failed required work.
- Ensure publishing selects only records with eligible completed render artefacts.
- Add safe coordination for overlapping process executions while preserving existing atomic file replacement.
- Record effective model, prompt-template version, workflow hash, seed and generation settings with outputs.
- Review prompt-length measurement so its tokenizer/limit matches the intended downstream constraint or is documented as a proxy.
- Add a small fixed evaluation corpus and report structured-output validity, retries, processing time, image/readability checks and completion rates.
- Keep human review as part of image-quality assessment.
- Add a deterministic offline/demo path using bundled fixtures so the orchestration can be demonstrated without LM Studio, ComfyUI or Instagram.
- Tighten ComfyUI polling so individual requests cannot exceed the overall operation deadline.
- Integrate publishing reliability improvements from the shared Instagram component.

## Portfolio outcome
Demonstrate observable and reproducible AI workflow engineering rather than simply adding more model providers or features.

No implementation is included in this placeholder PR.