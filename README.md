# AI Quote Image Pipeline

A local-first Python pipeline for turning quotes into AI-generated social images.
It uses LM Studio for prompt and hashtag generation, ComfyUI for image rendering,
and can optionally publish completed images to Instagram.

## What the pipeline does

1. Pull quote data from Quotable into a local JSON corpus.
2. Generate prompt text + hashtags through LM Studio for each quote.
3. Render images in ComfyUI and overlay quote text.
4. Optionally post one generated image via `upload_photo` integration.

## Clean clone and submodules

Use a clean clone for release artifacts:

```bash
git clone --recurse-submodules https://github.com/Xzese/ai-quote-image-pipeline.git
cd ai-quote-image-pipeline
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
python -m pip install --require-hashes -r requirements-lock.txt
```

`requirements-lock.txt` pins and hashes the complete root, development, and
optional upload dependency set for reproducible Python 3.11+ installs. This
project uses Python's standard-library `smtplib`; no separate SMTP package is
required.

After intentionally changing a requirements file, regenerate the lock:

```bash
python -m piptools compile --allow-unsafe --generate-hashes \
  --output-file=requirements-lock.txt --strip-extras \
  requirements-dev.txt requirements.txt upload_photo/requirements.txt
```

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

Create a local `.env` from `.env.example`. A local `.env` may contain secrets and
service credentials and must never be committed, committed to logs, or shared.

```bash
cp .env.example .env
```

```ini
QUOTES_FILE_PATH=output/quotes.json
OUTPUT_IMAGE_PATH=output/images
OVERLAY_OUTPUT_PATH=output/images_text_overlay
UPLOAD_QUOTE_MAX_ATTEMPTS=3
UPLOAD_QUOTE_RETRY_BASE_SECONDS=2.0
```

### LM Studio configuration

```ini
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_MODEL=qwen/qwen3.5-9b
# `LM_STUDIO_MODEL` should usually be a model key like `qwen/qwen3.5-9b`.
# `https://lmstudio.ai/models/...` URLs are normalized to this key form by the script.
LM_STUDIO_PRESET=
LM_STUDIO_NATIVE_API_BASE_URL=
LM_STUDIO_CONTEXT_LENGTH=8192
LM_STUDIO_PARALLEL_WORKERS=4
```

### ComfyUI configuration

```ini
COMFYUI_URL=http://127.0.0.1:8000
COMFYUI_WORKFLOW_PATH=workflows/image_z_image_turbo.json
COMFYUI_ALLOW_GLOBAL_QUEUE_CLEAR=false
```

### Optional publishing configuration

```ini
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

The retry values apply to one run of the posting module.
Posting credentials are optional unless you run the posting module. After the
final failed posting attempt, the script uses the SMTP settings above to send one
failure alert.

## LM Studio

[LM Studio](https://lmstudio.ai/) runs the local language model used to turn
quotes into visual prompts and hashtags. See the
[LM Studio developer documentation](https://lmstudio.ai/docs/developer) for
installation, local-server, REST API, and OpenAI-compatible endpoint guidance.

1. Install LM Studio and start its local API server.
2. Select a model that supports structured JSON output.
3. Configure the `LM_STUDIO_*` values in `.env`.
4. Run `python -m quote_image_generator.get_prompt`.

Use environment settings for LM Studio; do not edit package constants for a
release run. On startup, `get_prompt.py` checks whether the configured model is
available. If it is missing, the script uses LM Studio's native API to download
and load it before continuing.

Prompt and hashtag generation uses schema-constrained JSON through the
OpenAI-compatible API instead of parsing free-form model output.

`LM_STUDIO_NATIVE_API_BASE_URL` is optional; leaving it blank causes
`get_prompt.py` to derive the native API base as `/api/v1` from
`LM_STUDIO_BASE_URL`.

`LM_STUDIO_CONTEXT_LENGTH` sets the context window cap used when generating
prompts/hashtags. The script clamps the configured value to the lower of this
setting and the model-reported maximum context length.

## ComfyUI

[ComfyUI](https://comfy.org/) renders the image workflow after LM Studio has
generated the visual prompt. Use the
[official ComfyUI documentation](https://docs.comfy.org/) for installation and
local API guidance, and the
[workflow documentation](https://docs.comfy.org/development/core-concepts/workflow)
for an explanation of node-based workflows.

1. Install and start a local ComfyUI instance.
2. Make its API reachable at the configured `COMFYUI_URL`.
3. Install the model files required by the bundled workflow:
   - `ae.safetensors`
   - `qwen_3_4b.safetensors`
   - `z_image_turbo_bf16.safetensors`
4. Keep `COMFYUI_WORKFLOW_PATH` pointed at
   `workflows/image_z_image_turbo.json`, or provide another API-format workflow.
5. Run `python -m quote_image_generator.get_image`.

The default prompt seed behavior is controlled in the script. Keep the defaults
unless you intentionally want to change image reproducibility. This project
uses a local ComfyUI API rather than the ComfyUI Cloud API.

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
- `lmstudio` model download never completes / remains `loading`
  → Confirm `LM_STUDIO_NATIVE_API_BASE_URL` reaches the local native API
  (`/api/v1` by default), check model download permissions, and ensure disk + RAM
  headroom are sufficient.
- `LM Studio ping failed` or `model load failed`
  → Verify the requested model identifier is correct, wait until load completes,
  and confirm the model is large enough to support structured output.
- `structured output` / `response_format` errors or empty JSON responses
  → Some smaller models (notably <7B) may not support schema-constrained JSON
  outputs. Use a supported model or increase model size in `LM_STUDIO_MODEL`.
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

## Governance

- [CONTRIBUTING](./CONTRIBUTING.md)
- [SECURITY](./SECURITY.md)
- [CODE_OF_CONDUCT](./CODE_OF_CONDUCT.md)
- [CHANGELOG](./CHANGELOG.md)

## Packaging/legal checklist

- `.env` is local configuration and may contain credentials and tokens; do not commit it or any copied secrets.
- Generated images are AI-assisted outputs; downstream publication rights are the distributor’s responsibility.
- Quote text from Quotable is source-attributed and publication rights are the distributor’s responsibility.
- `ae.safetensors`, `qwen_3_4b.safetensors`, and `z_image_turbo_bf16.safetensors` are model assets that are **not** distributed from this repo by default.
- See `THIRD_PARTY_NOTICES.md` for full license and compliance guidance.
