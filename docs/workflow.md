# Workflow outcomes and reproducibility

Run the complete local workflow with:

```bash
python -m quote_image_generator.pipeline
```

Publishing is optional. Review the completed images before requesting a post:

```bash
python -m quote_image_generator.pipeline --stage publish
```

`--publish` enables one post at the end of a complete run. The existing
`get_prompt`, `get_image`, and `upload_quote_photo` modules call the same runner
for their respective stages. Quote retrieval remains a separate step.

## Outcomes and exit codes

Each requested prompt and render stage reports `completed`, `skipped`, `failed`,
`blocked`, or `cancelled` for each quote. `skipped` means a matching completed
receipt was reused; `blocked` means the prerequisite prompt has no matching
completed receipt. A prompt is complete only when both its visual prompt and
hashtags pass validation and the updated corpus has been saved. A render is
complete only after the final JPEG has been decoded and its hash recorded.

Publishing reports `published`, `skipped`, `failed`, `unknown`, or `cancelled`.
An empty selection is skipped without requesting credentials. Configuration or
preparation failure prevents a publication attempt. Any unconfirmed external
operation remains unknown and is never replayed automatically.

| Exit | Meaning |
| --- | --- |
| `0` | All requested work completed or validly skipped |
| `1` | Failure, blocked work, partial success, or unknown publication |
| `75` | Another cooperating process owns the corpus or output directory |
| `130` | Work was cancelled |

Successful items survive another item's failure. A later run retries incomplete
prompt/render stages and reuses valid completed stages. Cancellation preserves
completed work and stops scheduling further items. LM Studio calls already in
progress retain their existing request timeout; ComfyUI I/O can be terminated.

The final stdout line is the JSON run summary. It includes per-item outcomes,
status counts, elapsed time, prompt word counts, completed prompt count, eligible
render count, and `human_review_required`. Prompt diagnostics use stderr.

## Local state and coordination

For `output/quotes.json`, the runner writes:

- `output/quotes.json.state.json`: stage receipts and publication journal.
- `output/quotes.json.summary.json`: the latest completed run summary.
- `output/quotes.json.lock`: the cross-process corpus lock.

State and corpus writes use atomic replacement and flush their contents before
completion is reported. POSIX also flushes the parent directory. Keep the state
file with its corpus: deleting it loses publication ownership. Corrupt or
unsupported state fails closed.

All stage CLIs share the corpus lock. Image-directory locks also prevent runs
with different corpora from writing the same output directories concurrently.
LM Studio startup and owned-model unloading share a local service lock; an
already-loaded instance is reused and is never unloaded by this runner. Stages
process quotes sequentially. The older `LM_STUDIO_PARALLEL_WORKERS` setting is
accepted for configuration compatibility but does not control this runner.

These locks coordinate this checkout's cooperating processes on a local
filesystem. Separate corpora have separate publication journals. Separate
machines, copied state, third-party publishing tools, and filesystems without
reliable locking require external coordination; this is not exactly-once
remote delivery.

## Publication ownership and recovery

Selection requires matching source, prompt and render receipts, a matching
artefact hash, and a path beneath the configured final-image directory. Legacy
files alone are not eligible. Run the prompt and render stages to create fresh
receipts. Source or prompt edits invalidate dependent renders; model, template,
workflow, font or seed changes invalidate the affected stage's cache.

The journal claims an operation before object upload or any Meta call. The claim
is keyed by account and quote source, so changing the render seed does not replay
a confirmed publication. The container ID is saved before waiting for readiness
and before the single `media_publish` call. The adapter waits for `FINISHED`;
`IN_PROGRESS` is bounded by a 60-second readiness deadline. Other states and
readiness failures require reconciliation. This follows Meta's
[container status API](https://www.postman.com/meta/instagram/request/munmruq/get-ig-container-status).

An unknown operation blocks further publishing for that account in this corpus,
even if its quote has since changed or disappeared. A crash or a failure to save
remote confirmation leaves the durable claim in place. Inspection and
notification failures never cause another post attempt. Signed object URLs,
tokens, credentials and provider response bodies are not saved in the journal
or failure summary.

Inspect the account and the recorded container before recording a verified
outcome. These commands only update local state; they do not contact Meta:

```bash
# A publication was confirmed remotely; preserve its media ID.
python -m quote_image_generator.reconcile output/quotes.json OPERATION_ID \
  --media-id CONFIRMED_MEDIA_ID

# Only after verifying that no publication occurred: permit a fresh attempt.
python -m quote_image_generator.reconcile output/quotes.json OPERATION_ID \
  --not-published
```

A reconciled attempt remains in journal history. Never use `--not-published`
merely because a request timed out or a post is not immediately visible.

## Generation records

Prompt receipts record the requested LM Studio model, loaded instance ID when
available, preset, context cap, template version, temperature, output-token
budget, word measurement rule, source/output hashes, timestamps and duration.
Render receipts record model filenames by loader node, the base and submitted
workflow hashes, actual generation prompt, seed, dimensions, steps, CFG, font
hash, overlay version, ComfyUI prompt ID, final file hash and duration.

Set `GENERATION_SEED` to a nonnegative integer; the default is `42`. Changing it
causes a fresh render. Model filenames and identifiers do not prove identical
weight contents. GPU kernels, provider versions and model updates can still
change a live result; the metadata documents the inputs, not a guarantee of
bit-identical diffusion output.

The prompt limit is **50 whitespace-separated words**, using Python
`str.split()`. It does not estimate LM Studio or diffusion-model tokens. The
older unrelated BERT tokenizer and its `transformers` dependency were removed.
The atmosphere suffix is added at rendering time and recorded in the submitted
prompt. Check model context limits separately when configuring a workflow.

## ComfyUI deadline

`COMFYUI_DEADLINE_SECONDS` defaults to `300` and covers submission, history
polling and download for one image. All three share a monotonic deadline.
Requests run in disposable child processes, so DNS, connection establishment,
and slowly arriving response bodies cannot extend the deadline indefinitely.
The child is terminated and reaped on expiry or cancellation.

A separate cleanup request has a maximum two-second budget and deletes only the
known job ID. This runner never uses the global interrupt or queue-clear API:
queue inspection and global interruption cannot atomically establish job
ownership. A server job may finish after the client stops, especially if queue
submission timed out before its ID was received. Such a job is never treated as
a completed local artefact.

## Fixed evaluation corpus and offline demo

`fixtures/evaluation.json` contains six authored records covering short and long
text, Unicode, punctuation, calm imagery and energetic imagery. The text and
expected prompt/hashtag fixtures are versioned; they do not depend on Quotable.

```bash
python -m quote_image_generator.pipeline --offline --output output/demo
```

The demo uses fixture prompts and deterministic local backgrounds, then runs
the real overlay, receipt, cache and summary code. It needs no credentials,
model download, GPU or network. Publishing is disabled. A repeat reuses valid
receipts. Separate clean output directories produce identical JPEG hashes with
the pinned dependencies and bundled font. Run IDs, paths, timestamps and
elapsed durations are deliberately specific to each run.

For an evaluation against the configured live providers, copy the fixed corpus
rather than modifying the fixture:

```bash
mkdir -p output
cp fixtures/evaluation.json output/evaluation.json
QUOTES_FILE_PATH=output/evaluation.json python -m quote_image_generator.pipeline
```

Compare completion counts, failures, per-stage durations, word counts and
eligible artefacts in the summary and receipts. Record model/workflow/seed
changes with the comparison. Human review must assess quote relevance, text
legibility, overlay fit, unwanted people/text, visual defects and suitability
for publication. Fixture and CI completion do not assess diffusion quality.

## Validation boundaries

Automated coverage includes real slow HTTP responses, cross-process contention,
restart recovery, partial failures, corrupt/tampered files, stale source and
configuration, durable publication checkpoints, confirmation persistence
failure, reconciliation and deterministic offline outputs. The pinned
publishing submodule's suite runs in a separate Python process.

Live LM Studio, ComfyUI, object storage and Meta compatibility still require an
operator smoke test with the intended configuration. The automated workflow
never publishes a real image.
