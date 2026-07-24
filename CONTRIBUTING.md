# Contributing

Thanks for your interest in improving QuoteImageGenerator.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-lock.txt
```

## Code quality and checks

Run these checks before opening a PR:

```bash
python -m compileall -q .
ruff format --check --exclude upload_photo .
ruff check --exclude upload_photo .
pytest -q
pip-audit -r requirements-lock.txt
```

## Workflow

1. Keep PRs scoped and document the issue and resolution clearly.
2. Update docs when user-facing behavior changes.
3. Prefer small, targeted commits.
4. Do not modify `upload_photo` unless your change explicitly affects it.

## Tests in `upload_photo`

If your change touches `upload_photo`, run the relevant module checks in that
submodule before requesting review. Dependency automation for that code belongs
in its separate `Post_To_Instagram` repository because this project tracks it as
a Git submodule.
