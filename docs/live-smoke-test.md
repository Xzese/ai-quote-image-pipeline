# Small-model live smoke test

The generation pipeline was tested on 2026-10-04 with real local inference on
an NVIDIA RTX 5060 Ti (16 GB), using Python 3.11.16 for this project:

| Component | Tested configuration |
| --- | --- |
| LM Studio | Headless llmster 0.0.25-1, llama.cpp CUDA runtime 2.41.0, local API on 1234 |
| Ollama | 0.33.3, local API on 11434 |
| Text model | Qwen 3 0.6B Q4_K_M, 522,640,096 bytes, context cap 2048 |
| ComfyUI | 0.38.0, PyTorch 2.14.1+cu130, Python 3.12.15, local API on 8188 |
| Image model | Stable Diffusion 1.5 fp16 UNet, CLIP and VAE; standard nodes, custom nodes disabled |
| Generation | 512×512, Euler/normal, 10 steps, CFG 7, seed 42 |

## Results

- The normal quote-fetch CLI retrieved 15 Quotable pages, removed 37 duplicate
  IDs and saved 2,090 validated quotes. The existing HTTP endpoint worked;
  its HTTPS endpoint had an expired certificate. Certificate checks were not disabled.
- Each LLM provider completed the six evaluation records plus one newly fetched
  quote: seven prompts, seven hashtag sets and seven real ComfyUI renders, with
  zero failed stages. The initial seven-record runs took approximately 20 seconds
  each. These were real generations, not fixture prompt callbacks.
- Final JPEGs decoded at 512×512. Visual inspection of the initial samples and
  long/Unicode examples confirmed readable, fitting quote and author overlays.
- LM Studio loaded the requested small model and unloaded the instance owned by
  the run. Ollama never called LM Studio's model-management endpoints.
- With all three test servers stopped, both seven-record reruns succeeded and
  skipped all prompt/render stages. All 14 JPEG hashes stayed unchanged.
- The updated default batch orchestration was also tested with both real LLM
  providers and ComfyUI against a controlled two-quote HTTP source. Observed order
  was fetch → both prompts → model release → both renders. Provider model-list
  checks confirmed the owned text model was unloaded before each render began.
  The per-quote Ollama test observed fetch → prompt/image → prompt/image instead.
  Refreshing the same two records then reused all prompt/render receipts.

These results establish local API integration, validation, rendering, overlays
and receipt reuse under this configuration. The 0.6B model often repeated generic
forest imagery and produced weak hashtags. Use a larger model and review outputs
for quote relevance and publication quality. The default Qwen 3.5/Z-Image Turbo
models were not tested. No real S3/R2 upload, Meta publication or SMTP delivery
was attempted.

## Reproduce the small image setup

Use an existing GPU-compatible ComfyUI installation. The alternate workflow uses
the fp16 components from the
[Stable Diffusion 1.5 model repository](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5).
Download them into these ComfyUI model directories (about 2.1 GB total):

```bash
# Run from your ComfyUI directory. These names match workflows/image_sd15.json.
mkdir -p models/diffusion_models models/text_encoders models/vae
curl -fL https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/unet/diffusion_pytorch_model.fp16.safetensors \
  -o models/diffusion_models/sd15_unet.fp16.safetensors
curl -fL https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/text_encoder/model.fp16.safetensors \
  -o models/text_encoders/sd15_clip.fp16.safetensors
curl -fL https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/vae/diffusion_pytorch_model.fp16.safetensors \
  -o models/vae/sd15_vae.fp16.safetensors
python main.py --listen 127.0.0.1 --port 8188 --disable-all-custom-nodes
```

Observe the model repository's licence when downloading or using its weights.
No weights are included in this project.

From this project, configure a **separate test corpus and output directories**:

```bash
mkdir -p output/smoke
cp fixtures/evaluation.json output/smoke/quotes.json
export QUOTES_FILE_PATH=output/smoke/quotes.json
export OUTPUT_IMAGE_PATH=output/smoke/images
export OVERLAY_OUTPUT_PATH=output/smoke/final
export COMFYUI_URL=http://127.0.0.1:8188
export COMFYUI_WORKFLOW_PATH=workflows/image_sd15.json
export COMFYUI_WIDTH=512 COMFYUI_HEIGHT=512 COMFYUI_STEPS=10 COMFYUI_CFG=7
export GENERATION_SEED=42
```

## Ollama

Start Ollama (`ollama serve`, if it is not already running), then:

```bash
ollama pull qwen3:0.6b
export LLM_PROVIDER=ollama
export OLLAMA_MODEL=qwen3:0.6b OLLAMA_CONTEXT_LENGTH=2048
python -m quote_image_generator.pipeline --skip-fetch
```

The native `/api/show` check rejects embedding-only models. `/api/chat` uses a
JSON schema, non-streaming output, bounded generation and capability-aware
thinking control. JSON and all existing prompt/hashtag constraints are validated
locally. See [Ollama's API documentation](https://docs.ollama.com/api/chat).

## LM Studio

Start the LM Studio local API, using either the app or the
[official headless runtime](https://lmstudio.ai/docs/developer/core/headless).
Install a Qwen 3 0.6B Q4_K_M GGUF. Use its exact key from `/api/v1/models` or
`lms ls`; the imported model in this test had key `qwen3-0.6b`.

```bash
export LLM_PROVIDER=lm_studio
export LM_STUDIO_MODEL=qwen3-0.6b LM_STUDIO_CONTEXT_LENGTH=2048
python -m quote_image_generator.pipeline --skip-fetch
```

Changing providers intentionally invalidates the existing prompt and render
receipts. Use separate test directories when comparing providers.

## Cache check

Repeat the same command with unchanged configuration. Every prompt and render
should report `skipped`, and JPEG hashes should stay unchanged. A stronger check
is to stop only the test servers you started and repeat: valid receipts should
still succeed without contacting any provider when using `--skip-fetch`.
Keep each corpus's `.state.json`
file when repeating; the `.summary.json` file always describes the latest run.
