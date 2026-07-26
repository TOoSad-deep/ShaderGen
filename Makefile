.PHONY: all setup setup-memory-postgres dev dev-agent dev-backend dev-node-lab dev-frontend check docs-check format lint test tests test_watch integration_tests test-memory-postgres test-scene-mvp-ui docker_tests help extended_tests

# Default target executed when no arguments are given to make.
all: help

# Define a variable for the test file path.
TEST_FILE ?= tests/unit_tests/

setup:
	uv sync
	uv run playwright install chromium
	npm --prefix frontend install

setup-memory-postgres:
	uv run python scripts/setup_memory_postgres.py

dev:
	@echo 'Run one service per terminal: make dev-agent | make dev-backend | make dev-node-lab | make dev-frontend'

dev-agent:
	uv run langgraph dev

dev-backend:
	uv run uvicorn backend.app.main:app --reload --port 8088

dev-node-lab:
	uv run uvicorn nodelab_service.main:create_app --factory --reload --port 8090

dev-frontend:
	npm --prefix frontend run dev

test:
	uv run pytest $(TEST_FILE)

tests: test

integration_tests:
	uv run pytest tests/integration_tests

test-memory-postgres:
	uv run python scripts/run_memory_postgres_test.py

test-scene-mvp-ui:
	npm --prefix frontend run e2e:scene-mvp

check:
	uv run pytest tests/unit_tests
	uv run python scripts/docs_check.py
	uv run langgraph validate
	npm --prefix frontend run build

docs-check:
	uv run python scripts/docs_check.py

test_watch:
	uv run python -m ptw --snapshot-update --now . -- -vv tests/unit_tests

test_profile:
	uv run pytest -vv tests/unit_tests/ --profile-svg

extended_tests:
	uv run pytest --only-extended $(TEST_FILE)


######################
# LINTING AND FORMATTING
######################

# Define a variable for Python and notebook files.
PYTHON_FILES=src/
MYPY_CACHE=.mypy_cache
lint format: PYTHON_FILES=.
lint_diff format_diff: PYTHON_FILES=$(shell git diff --name-only --diff-filter=d main | grep -E '\.py$$|\.ipynb$$')
lint_package: PYTHON_FILES=src
lint_tests: PYTHON_FILES=tests
lint_tests: MYPY_CACHE=.mypy_cache_test

lint lint_diff lint_package lint_tests:
	uv run ruff check .
	[ "$(PYTHON_FILES)" = "" ] || uv run ruff format $(PYTHON_FILES) --diff
	[ "$(PYTHON_FILES)" = "" ] || uv run ruff check --select I $(PYTHON_FILES)
	[ "$(PYTHON_FILES)" = "" ] || uv run mypy --strict $(PYTHON_FILES)
	[ "$(PYTHON_FILES)" = "" ] || mkdir -p $(MYPY_CACHE) && uv run mypy --strict $(PYTHON_FILES) --cache-dir $(MYPY_CACHE)

format format_diff:
	uv run ruff format $(PYTHON_FILES)
	uv run ruff check --select I --fix $(PYTHON_FILES)

spell_check:
	codespell --toml pyproject.toml

spell_fix:
	codespell --toml pyproject.toml -w

######################
# HELP
######################

help:
	@echo '----'
	@echo 'setup                        - install Python and frontend dependencies'
	@echo 'setup-memory-postgres        - initialize LangGraph PostgreSQL persistence tables'
	@echo 'dev-agent                    - run LangGraph dev server'
	@echo 'dev-backend                  - run FastAPI backend on port 8088'
	@echo 'dev-node-lab                 - run standalone Node Lab on port 8090'
	@echo 'dev-frontend                 - run Vite frontend'
	@echo 'check                        - run unit tests, LangGraph validation, frontend build'
	@echo 'docs-check                   - verify harness docs and architecture boundaries'
	@echo 'test-memory-postgres         - verify Shader Memory against PostgreSQL'
	@echo 'test-scene-mvp-ui             - verify the scene_mvp pipeline summary in isolated Chromium'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'tests                        - run unit tests'
	@echo 'test TEST_FILE=<test_file>   - run all tests in file'
	@echo 'test_watch                   - run unit tests in watch mode'
