# Publishing integration

The publishing implementation now uses the shared receipt-backed runner. See
[workflow outcomes and reproducibility](workflow.md) for selection, ownership,
reconciliation, exit codes, metadata and validation boundaries.

The `upload_photo` submodule remains pinned to the hardened implementation at
`caafd33f4bff90635fc529949c827f03cca85f4d`. The pipeline adds durable operation
ownership, container checkpoints and bounded readiness polling around that
implementation. It does not automatically retry a publication.

Configure `GRAPH_API_VERSION` and `IG_BUSINESS_USER_ID` explicitly. Verify the
Graph API version against the Meta application. Token, object-storage and
optional SMTP settings remain supported. The old `UPLOAD_QUOTE_MAX_ATTEMPTS`
and `UPLOAD_QUOTE_RETRY_BASE_SECONDS` settings are obsolete.

Only completed images with matching receipts are eligible. Existing images from
older runs must be regenerated through the prompt/render stages. Inspect image
quality before enabling publication, and retain the corpus state file between
scheduler runs.
