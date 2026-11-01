.PHONY: install lint format typecheck test check schema demo

install:
	uv sync --all-extras

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src/

test:
	uv run pytest

check: lint
	uv run ruff format --check .
	uv run mypy src/
	uv run pytest

schema:
	uv run python -c 'import json; from account_research.schemas.account import AccountRecord; open("docs/account_record.schema.json", "w").write(json.dumps(AccountRecord.model_json_schema(), indent=2) + chr(10))'

demo:
	uv run python app/gradio_app.py
