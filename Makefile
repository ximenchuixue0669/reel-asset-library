.PHONY: dev frontend check test

frontend:
	npm --prefix frontend ci
	npm --prefix frontend run build

dev: frontend
	uv run --locked python -m reel_library.app

check: frontend
	uv run --locked ruff format --check
	uv run --locked ruff check
	$(MAKE) test

test:
	uv run --locked pytest -q
