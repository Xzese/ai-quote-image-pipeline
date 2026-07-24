# Third-party notices for public release

This project includes third-party services, assets, and data. This file is the release-time legal starting point.

## Runtime dependencies

Python runtime packages are listed in `requirements.txt`:

- Pillow
- requests
- transformers
- openai
- python-dotenv

Review and include upstream license notices before redistribution.

## LM Studio / model workflow notices

Workflow assets are not bundled by this repository:

- LM Studio model endpoint (default): `qwen/qwen3.5-9b`
- ComfyUI workflow file in-repo: `workflows/image_z_image_turbo.json`
- External model checkpoint names required by the workflow:
  - `ae.safetensors`
  - `qwen_3_4b.safetensors`
  - `z_image_turbo_bf16.safetensors`

These model files are expected to be supplied in the operator’s local runtime environment.
They must be treated as separately licensed components and should not be distributed from this repo.

## Quote source

Quotes are pulled from `api.quotable.io` via the `quote_image_generator.get_quotes` module.

- Source license/usage terms are controlled by the Quotable API provider.
- This repository/operator remains responsible for validating attribution and commercial-use permissions before downstream publication.

## Font assets

`assets/fonts/Alegreya-VariableFont.ttf` is included for text overlay rendering.
It is distributed under the **SIL Open Font License 1.1 (OFL-1.1)**.

- Bundled license text: [`licenses/Alegreya-SIL-OFL-1.1.txt`](licenses/Alegreya-SIL-OFL-1.1.txt)

## Generated output legal responsibility

- Quote attributions and text in output images are your responsibility.
- Generated-image output files (`output/images/`, `output/images_text_overlay/`) remain operator-owned content, and all publication/use rights are the distributor’s responsibility.

## Optional Instagram integration

`upload_photo/` is a git submodule and is governed by its own dependency and license set.
Any packaging that includes that module should incorporate its corresponding notices.
