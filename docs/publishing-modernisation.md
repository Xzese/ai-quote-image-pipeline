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

Only completed images with matching receipts are eligible. The first
publish-only run automatically imports matching legacy `1024x1024` RGB JPEGs,
preserving their saved prompts/hashtags and recording file hashes with unknown
original generation metadata. Set `LEGACY_IMAGE_AUTO_IMPORT=false` to opt out.
Use `python -m quote_image_generator.legacy_import --dry-run` inside the
publishing container to preview the one-time import. The standalone importer
does not publish or contact external services. Existing receipts and publication
claims are preserved; skipped files require normal regeneration. Inspect image
quality before enabling publication, retain the state file between scheduler
runs, and keep the backup until the migrated deployment is confirmed.
