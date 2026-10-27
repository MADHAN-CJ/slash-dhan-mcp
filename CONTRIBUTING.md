# Contributing

- Python 3.12 with [uv](https://docs.astral.sh/uv/); always run tools through `uv run`.
- Before opening a pull request: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.
  CI runs the same commands.
- Install the hooks once with `uv run pre-commit install`.
- The `slashdhan` library is read-only by construction. Pull requests that add order placement,
  modification or cancellation will not be merged.
- Never commit credentials. `.env` is ignored; document new settings in `.env.example`.
