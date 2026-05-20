.PHONY: install run test clean

PY = .venv/bin/python
PIP = .venv/bin/pip

# One-time setup: virtualenv, dependencies, Chromium browser.
install:
	python3.11 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PY) -m playwright install chromium
	@test -f .env || cp .env.example .env
	@echo ""
	@echo "Setup done. Put your GEMINI_API_KEY in .env, then: make run"

# Start the app (single documented run command).
run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Author's test suite.
test:
	$(PY) -m pytest -q

clean:
	rm -rf .pytest_cache app/__pycache__ app/adapters/__pycache__ tests/__pycache__ founderhunt.db
