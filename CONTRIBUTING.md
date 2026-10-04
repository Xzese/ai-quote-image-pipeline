# Contributing

Thanks for your interest in improving AI Quote Image Pipeline.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .
```

## Code quality and checks

Run these checks before opening a PR:

```bash
python -m compileall -q src tests
ruff format --check --exclude upload_photo .
ruff check --exclude upload_photo .
python -m pytest -q tests
(cd upload_photo && python -m pytest -q tests)
python -m quote_image_generator.pipeline --offline --output output/demo
pip-audit -r requirements-lock.txt
```

## Workflow

Application modules live in `src/quote_image_generator/` and their tests in
`tests/`. Tests import the editable-installed package; avoid adding repository
directories to `sys.path`. Keep shared fonts, workflows and fixtures in their
existing repository directories. The `upload_photo/` submodule has its own
layout and tests and is not reorganised as part of this project.

1. Keep PRs scoped and document the issue and resolution clearly.
2. Update docs when user-facing behavior changes.
3. Prefer small, targeted commits.
4. Do not modify `upload_photo` unless your change explicitly affects it.

## Tests in `upload_photo`

If your change touches `upload_photo`, run the relevant module checks in that
submodule before requesting review. Dependency automation for that code belongs
in its separate `Post_To_Instagram` repository because this project tracks it as
a Git submodule.
