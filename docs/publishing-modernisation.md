# Publishing stage: first implementation

The portfolio-modernisation branch starts the pipeline work at the externally visible publishing step. It does not yet implement outcomes for prompt generation or rendering.

## Behaviour changes

The stage selects only existing, non-empty rendered files beneath the configured output directory. Missing renders produce a `skipped` summary without a token-provider or publishing call. The optional publisher is imported only when needed.

The result is a JSON summary with `status`, `quote_id`, `media_id` and `excluded`. Confirmed publication and no eligible work return exit code zero. Pre-publication failure or an unconfirmed publication return non-zero. A falsey or malformed publishing result no longer counts as success.

A publishing exception is not automatically retried. The old `UPLOAD_QUOTE_MAX_ATTEMPTS` and `UPLOAD_QUOTE_RETRY_BASE_SECONDS` settings are no longer used by this stage. Notification and output failures do not cause a confirmed post to be repeated.

The `upload_photo` submodule is pinned to the first reliability implementation from Xzese/Post_To_Instagram PR #1 (caafd33f4bff90635fc529949c827f03cca85f4d). Update submodules after checkout. That implementation requires explicit `GRAPH_API_VERSION` and `IG_BUSINESS_USER_ID` settings. Verify the Graph API version with the Meta application; no current version is assumed.

## Safety limits

This is single-invocation protection, not exactly-once delivery. A later scheduler run, another device or another process can still publish the same image. An unknown result requires operator reconciliation. Persistent ownership, durable remote identifiers, deduplication and container-readiness polling are not complete. Do not use this draft for unattended production publishing.

## Validation

Fifteen isolated publishing-stage tests passed locally with filesystem fixtures and injected external effects. The entry-point module passed syntax compilation. The full existing suite, dependency installation, Ruff formatting and live LM Studio/ComfyUI/Meta integration were not verified in the local environment. Keep all existing CI checks enabled and resolve failures before merge.

## Remaining work

Add all-stage state and partial-success reporting, persistent cross-process coordination, reproducibility metadata, the fixed evaluation corpus and offline full-pipeline demo. Bound ComfyUI calls by the overall deadline. Expand entry-point/configuration regression tests and verify the shared publisher end to end.
