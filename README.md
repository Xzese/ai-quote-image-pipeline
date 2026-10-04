# AI Quote Image Pipeline

<p align="center">
  <a href="https://github.com/Xzese/ai-quote-image-pipeline/stargazers"><img src="https://img.shields.io/github/stars/Xzese/ai-quote-image-pipeline?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/Xzese/ai-quote-image-pipeline/commits/main"><img src="https://img.shields.io/github/last-commit/Xzese/ai-quote-image-pipeline?style=flat-square" alt="Last commit"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/Xzese/ai-quote-image-pipeline"><img src="https://img.shields.io/github/languages/top/Xzese/ai-quote-image-pipeline?style=flat-square" alt="Top language"></a>
</p>

A local-first Python pipeline for turning quotes into AI-generated social images.
It uses LM Studio or Ollama for prompt and hashtag generation, ComfyUI for image rendering,
and can optionally publish completed images to Instagram.

## What the pipeline does

1. Pull quote data from Quotable into a local JSON corpus.
2. Generate prompt text + hashtags through the selected local LLM for each quote.
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
python -m pip install --no-deps --no-build-isolation -e .
```

`requirements-lock.txt` pins and hashes the complete root, development, and
optional upload dependency set for reproducible Python 3.11+ installs. This
project uses Python's standard-library `smtplib`; no separate SMTP package is
required.

The second install command registers the local project in editable mode using
the build tools already pinned in the lockfile. Run it once after cloning or
updating to the `src` layout. Existing `python -m quote_image_generator...`
commands then work without setting `PYTHONPATH`. This application runs from its
checkout: `.env`, assets, workflows, fixtures and outputs resolve relative to the
repository root, including when a command starts from another directory.

After intentionally changing a requirements file, regenerate the lock:

```bash
python -m piptools compile --allow-unsafe --generate-hashes \
  --output-file=requirements-lock.txt --strip-extras \
  requirements-dev.txt requirements.txt upload_photo/requirements.txt
```

## Repository layout

```text
src/
  quote_image_generator/ # Application package and executable modules
tests/                   # Application tests
pyproject.toml           # Editable installation and test discovery
assets/fonts/            # Bundled Alegreya font
workflows/               # ComfyUI workflow JSON
fixtures/                # Authored evaluation corpus
docs/                    # Workflow contracts and smoke-test instructions
licenses/                # Third-party licence texts
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
PIPELINE_MODE=batch
PIPELINE_FETCH_QUOTES=true
QUOTES_ENDPOINT_URL=http://api.quotable.io/quotes
```

### LM Studio configuration

```ini
LLM_PROVIDER=lm_studio
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_MODEL=qwen/qwen3.5-9b
# `LM_STUDIO_MODEL` should usually be a model key like `qwen/qwen3.5-9b`.
# `https://lmstudio.ai/models/...` URLs are normalized to this key form by the script.
LM_STUDIO_PRESET=
LM_STUDIO_NATIVE_API_BASE_URL=
LM_STUDIO_CONTEXT_LENGTH=8192
```

### Ollama configuration

Start Ollama and install a chat model, for example `ollama pull qwen3:0.6b`.
Then select it in `.env`:

```ini
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:0.6b
OLLAMA_CONTEXT_LENGTH=8192
```

The same pipeline and stage commands work with either provider. Ollama uses its
native [structured-output API](https://docs.ollama.com/capabilities/structured-outputs)
for the readiness check, visual prompts and hashtags. The model must already be
installed and support chat/completion; embedding models are rejected. Thinking is
disabled for models that advertise that capability. Requests have a bounded
timeout and support cancellation. There is no automatic provider fallback or
model download. In batch mode, the pipeline checks Ollama's running models before
generation and unloads its newly loaded model before rendering. Pre-existing
loaded models remain untouched. Per-quote mode uses a five-minute keep-alive.

### ComfyUI configuration

```ini
COMFYUI_URL=http://127.0.0.1:8000
COMFYUI_WORKFLOW_PATH=workflows/image_z_image_turbo.json
COMFYUI_DEADLINE_SECONDS=300
COMFYUI_WIDTH=1024
COMFYUI_HEIGHT=1024
COMFYUI_STEPS=10
COMFYUI_CFG=1
GENERATION_SEED=42
```

### Optional publishing configuration

```ini
# Optional Instagram posting
GRAPH_API_VERSION=
ACCESS_TOKEN=
ACCESS_TOKEN_EXPIRY=
IG_BUSINESS_USER_ID=
LOG_FILE=output/instagram.log

# Optional remote Facebook token provider
FACEBOOK_TOKEN_API_BASE_URL=
FACEBOOK_TOKEN_API_KEY=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=

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

Posting credentials are optional unless you request publishing. Publication is
attempted once; an unconfirmed outcome is stored durably and requires operator
reconciliation. Optional SMTP settings send a redacted notification. See the
[workflow contracts and recovery guide](docs/workflow.md).

#### Remote Facebook token provider

Static `ACCESS_TOKEN` and `ACCESS_TOKEN_EXPIRY` values remain supported. As an
optional alternative, the uploader can retrieve the current token from a
server-side Cloudflare Worker before it posts:

```ini
FACEBOOK_TOKEN_API_BASE_URL=https://facebook-token-worker.example.com
FACEBOOK_TOKEN_API_KEY=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

When `FACEBOOK_TOKEN_API_BASE_URL` is empty, no token-provider request is made.
When it is set, all four values above are required. The uploader sends a
`GET /api/token` request with the Cloudflare Access service-token headers and
the token API key as a bearer credential. A successful endpoint response has
this shape:

```json
{
  "accessToken": "<facebook-access-token>",
  "expiresAt": "2099-01-01T00:00:00.000Z",
  "expired": false
}
```

The retrieved Facebook access token is placed only in the current process
environment; it is not written back to `.env`. The client setting is named
`FACEBOOK_TOKEN_API_KEY` to distinguish it from other API credentials. If the
Worker itself names the matching secret `TOKEN_API_KEY`, use the same secret
value under the client-side name shown above.

Keep all three credentials out of source control and logs. The static token
settings are used only when the optional provider URL is not configured.

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

[ComfyUI](https://comfy.org/) renders the image workflow after the selected LLM has
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

`GENERATION_SEED` defaults to `42` and is recorded with the actual workflow and
model filenames. `COMFYUI_DEADLINE_SECONDS` bounds submission, polling and download
for each image. The project uses the local ComfyUI API.

For a smaller smoke test, `workflows/image_sd15.json` uses separate fp16 Stable
Diffusion 1.5 components and standard ComfyUI nodes. Set width/height to `512`,
steps to `10` and CFG to `7`. Dimensions must be positive multiples of eight;
CFG accepts decimals. See [the live smoke-test setup and results](docs/live-smoke-test.md)
for the model filenames, download commands and tested versions. A small LLM and
ten diffusion steps are suitable for checking connectivity, not assessing final
quote relevance or publication quality.

## Run order

Start the complete workflow with one command:

```bash
python -m quote_image_generator.pipeline
```

By default, `PIPELINE_MODE=batch` refreshes the quote corpus, generates prompts
and hashtags for **all quotes**, releases the text model loaded by this run, then
renders and overlays **all images**. Work remains sequential within each stage.

To generate a prompt and image for each quote before moving to the next, set:

```ini
PIPELINE_MODE=per_quote
```

You can also override it for one run with `--mode per_quote` or `--mode batch`.
Both modes fetch quotes first. An unchanged quote retains its generated prompt
and hashtags on refresh, so matching receipts continue to skip completed work.
Quotes no longer returned by the source remain in the corpus with their generated
fields. Existing order is preserved, matching IDs are updated, and new IDs are
appended. A refresh never removes saved quotes, including when the source returns
an empty list.
Fetch failures leave the saved corpus intact and stop generation.

Use `--skip-fetch` or `PIPELINE_FETCH_QUOTES=false` to use an existing corpus,
including custom quotes or a test sample:

```bash
python -m quote_image_generator.pipeline --skip-fetch
```

The individual `get_prompt`, `get_image`, and `upload_quote_photo` entry points
remain available, as does `get_quotes` for a standalone refresh. Explicit
`--stage prompt`, `--stage render` and `--stage publish` use the saved corpus and
never fetch quotes. They use the same receipts and locks. Review completed images
before running the optional publishing stage:

```bash
python -m quote_image_generator.pipeline --stage publish
```

Try the complete fixture-backed workflow without services or credentials:

```bash
python -m quote_image_generator.pipeline --offline --output output/demo
```

The runner emits a JSON summary and saves durable stage receipts beside the
quote corpus. Completed stages are reused only while their inputs and artefacts
match. Partial success exits nonzero while retaining successful work. Existing
images without receipts must be regenerated. Read the
[workflow and evaluation guide](docs/workflow.md) for exit codes, reproducibility
metadata, the fixed evaluation corpus, deadline behaviour and recovery commands.

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
├── quotes.json.state.json      # durable receipts and publication journal
├── quotes.json.summary.json    # latest measurable run summary
├── images/                     # ComfyUI base render output
│   └── <_id>1024x1024.png
└── images_text_overlay/        # final images with quote overlay
    └── <_id>1024x1024.jpeg
```

## Cancel behavior

SIGINT and SIGTERM stop new work and preserve completed receipts. ComfyUI
requests are terminated within their wall-clock deadline. Cleanup deletes only
the known job owned by this run; it never interrupts the global server queue.
LM Studio calls already in progress keep their configured request timeout.

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
- Pipeline tests: `python -m pytest -q tests`.
- Publishing submodule tests: `(cd upload_photo && python -m pytest -q tests)`.
- Keep the suites in separate Python processes so their module roots do not collide.

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
