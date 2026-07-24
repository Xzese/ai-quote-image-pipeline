# QuoteImageGenerator — Public release notes

## What this release includes

1. Pull quote data from Quotable into a local JSON corpus.
2. Generate prompt text + hashtags through LM Studio for each quote.
3. Render images in ComfyUI and overlay quote text.
4. Optionally post one generated image via `upload_photo` integration.

## Clean clone and submodules

Use a clean clone for release artifacts:

```bash
git clone --recurse-submodules <repository-url>
cd QuoteImageGenerator
git submodule update --init --recursive
```

To force refresh the submodule pointer before a release:

```bash
git submodule sync
git submodule update --init --recursive
```

## Setup and install

Python 3.11+ is required.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip

TMP_REQUIREMENTS=$(mktemp)
trap 'rm -f "$TMP_REQUIREMENTS"' EXIT
grep -iv '^secure-smtplib' upload_photo/requirements.txt > "$TMP_REQUIREMENTS"
python -m pip install -r "$TMP_REQUIREMENTS"    # installs upload-photo dependencies except secure-smtplib
python -m pip install -r requirements.txt         # applies your pinned requests/python-dotenv versions
```

This skips `secure-smtplib` because this repo relies on stdlib `smtplib`; root `requirements.txt` is installed last so its pinned versions win.

## Repository layout

```text
quote_image_generator/   # Python package and executable modules
assets/fonts/            # Bundled Alegreya font
workflows/               # ComfyUI workflow JSON
licenses/                # Third-party licence texts
tests/                   # Unit tests
upload_photo/            # Optional Instagram posting submodule
output/                  # Generated local data, ignored by Git
```

## Environment

Create `.env` and keep only non-sensitive values:

```bash
cp .env.example .env
```

```ini
QUOTES_FILE_PATH=output/quotes.json
OUTPUT_IMAGE_PATH=output/images
OVERLAY_OUTPUT_PATH=output/images_text_overlay
COMFYUI_URL=http://127.0.0.1:8000
COMFYUI_WORKFLOW_PATH=workflows/image_z_image_turbo.json
COMFYUI_ALLOW_GLOBAL_QUEUE_CLEAR=false

LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_MODEL=qwen/qwen3.5-9b
LM_STUDIO_PRESET=@local:no-thinking
LM_STUDIO_PARALLEL_WORKERS=4
UPLOAD_QUOTE_MAX_ATTEMPTS=3
UPLOAD_QUOTE_RETRY_BASE_SECONDS=2.0

# Optional Instagram posting
ACCESS_TOKEN=
ACCESS_TOKEN_EXPIRY=
IG_BUSINESS_USER_ID=
LOG_FILE=output/instagram.log

# Optional S3/R2 image hosting
S3_BUCKET_NAME=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_ENDPOINT=

# Optional posting-failure email alerts
SMTP_SERVER=
SMTP_PORT=
SENDER_EMAIL=
SENDER_PASSWORD=
RECIPIENT_EMAIL=
```

Use environment settings for LM Studio; do not edit package constants in a release run.
The retry values apply to one run of the posting module.
Posting credentials are optional unless you run the posting module. After the
final failed posting attempt, the script uses the SMTP settings above to send one
failure alert.

## LM Studio / model workflow

- Comfy workflow model files expected by the default pipeline:
  - `ae.safetensors`
  - `qwen_3_4b.safetensors`
  - `z_image_turbo_bf16.safetensors`
- Run ComfyUI with API access (`COMFYUI_URL`).
- `workflows/image_z_image_turbo.json` is the repository-relative default workflow.
- Default prompt seed behavior is in-script only; keep default parameters unless intentionally changed.

## Run order

1. Pull base quotes:

   ```bash
   python -m quote_image_generator.get_quotes
   ```

2. Generate prompt/hashtags:

   ```bash
   python -m quote_image_generator.get_prompt
   ```

3. Generate images + overlay:

   ```bash
   python -m quote_image_generator.get_image
   ```

4. Optional one-post upload (bounded by one run):

   ```bash
   python -m quote_image_generator.upload_quote_photo
   ```

Upload retry controls are set in `.env`:

```ini
UPLOAD_QUOTE_MAX_ATTEMPTS=3
UPLOAD_QUOTE_RETRY_BASE_SECONDS=2.0
```

## Quote schema

Expected input record format:

```json
{
  "_id": "abcdef123",
  "content": "The only way to do great work is to love what you do.",
  "author": "Steve Jobs"
}
```

`prompt` and `hashtags` are optional output fields and are added by the prompt module:

```json
{
  "_id": "abcdef123",
  "content": "The only way to do great work is to love what you do.",
  "author": "Steve Jobs",
  "prompt": "misty morning city skyline ...",
  "hashtags": "{#motivation #inspiration #quotes}"
}
```

## Output tree

Default release output (from the `.env` defaults):

```text
output/
├── quotes.json                 # updated quote dataset
├── images/                     # ComfyUI base render output
│   └── <_id>1024x1024.png
└── images_text_overlay/        # final images with quote overlay
    └── <_id>1024x1024.jpeg
```

## Cancel behavior

### Prompt generation

- Interrupting once triggers graceful shutdown and waits for running workers.
- Interrupting twice exits immediately.

### Image generation

- Interrupt request normally cancels work associated with this run (owned jobs).
- Set `COMFYUI_ALLOW_GLOBAL_QUEUE_CLEAR=true` only if you explicitly want a full queue clear instead of owned-job cancellation.

## Troubleshooting

- `ValueError: QUOTES_FILE_PATH is not set`  
  → Ensure `QUOTES_FILE_PATH=output/quotes.json` exists in `.env`.
- `requests`/connection failures to LM Studio  
  → Confirm `LM_STUDIO_BASE_URL` points to a running LM Studio local API.
- `ComfyUI API unreachable`  
  → Confirm `COMFYUI_URL` is reachable and workflow is loaded in the UI.
- `Queue stalls`  
  → Reduce batch size and confirm model checkpoints and GPU/VRAM.
- Missing output fonts  
  → Confirm `assets/fonts/Alegreya-VariableFont.ttf` is present.
- Missing `upload_photo` module  
  → Re-run submodule init/update and verify `upload_photo` is populated.

## Tests

- Manual smoke checks:
  - `python -m quote_image_generator.get_quotes`
  - `python -m quote_image_generator.get_prompt`
  - `python -m quote_image_generator.get_image`
  - `python -m quote_image_generator.upload_quote_photo` (single-post path)
  - `python -m quote_image_generator.sort_json [quotes-file]`
- Project test discovery:
  - `python -m pytest`

## Packaging/legal checklist

- Do not commit credentials (no API tokens, secrets, or personal accounts in `.env` or repository files).
- Generated images are AI-assisted outputs; downstream publication rights are the distributor’s responsibility.
- Quote text from Quotable is source-attributed and publication rights are the distributor’s responsibility.
- `ae.safetensors`, `qwen_3_4b.safetensors`, and `z_image_turbo_bf16.safetensors` are model assets that are **not** distributed from this repo by default.
- See `THIRD_PARTY_NOTICES.md` for full license and compliance guidance.
