.PHONY: install run test lint demo daily-content

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn autonomous_intelligence.api:app --reload

test:
	pytest -q

lint:
	ruff check src tests

demo:
	python -m autonomous_intelligence.cli "How will agentic AI change enterprise software?"

daily-content:
	python -m autonomous_intelligence.daily_content_cli
