.PHONY: install run test setup

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/playwright install chromium

setup: install
	cp -n .env.example .env || true

run:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

test:
	.venv/bin/pytest tests/ -q
