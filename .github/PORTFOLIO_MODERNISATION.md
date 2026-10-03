# AI pipeline maturity pass

Status: implementation started. Keep the PR in draft.

Implemented in this pass: explicit publishing outcomes, eligible-render selection, no automatic replay after an unconfirmed publication, optional dependency loading, redacted failure notification, and a pinned hardened publishing submodule. Fifteen isolated tests passed; the entry point passed syntax compilation.

This is not the complete pipeline plan. Prompt/render outcome state, cross-process locking, persistent publication ownership, model/workflow/seed metadata, tokenizer review, evaluation corpus, full offline demo, ComfyUI deadline changes, full CI and live integration remain open. See docs/publishing-modernisation.md for behaviour changes and validation limits.
