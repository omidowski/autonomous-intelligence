.PHONY: install run run-flask test lint demo daily-content

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn autonomous_intelligence.api:app --reload

run-flask:
	flask --app flask_app.wsgi run --debug --port 5001

test:
	pytest -q

lint:
	ruff check src flask_app tests

demo:
	python -m autonomous_intelligence.cli "How will agentic AI change enterprise software?"

daily-content:
	python -m autonomous_intelligence.daily_content_cli
