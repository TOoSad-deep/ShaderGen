.PHONY: all setup setup-memory-postgres dev dev-agent dev-backend dev-node-lab dev-frontend check docs-check format lint test tests test_watch integration_tests test-memory-postgres test-node-lab-ui test-scene-mvp-ui benchmark-ai-off benchmark-node-lab-ai-off benchmark-node-lab-model benchmark-png-to-shader benchmark-gate docker_tests help extended_tests

# Default target executed when no arguments are given to make.
all: help

# Define a variable for the test file path.
TEST_FILE ?= tests/unit_tests/
QUALITY_PRESET ?= balanced
MODEL_CALL_BUDGET ?= 80

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
	SHADERGEN_NODE_LAB_ENABLED=true uv run uvicorn backend.app.main:app --reload --port 8088

dev-frontend:
	npm --prefix frontend run dev

test:
	uv run pytest $(TEST_FILE)

tests: test

integration_tests:
	uv run pytest tests/integration_tests

test-memory-postgres:
	uv run python scripts/run_memory_postgres_test.py

test-node-lab-ui:
	npm --prefix frontend run e2e:node-lab

test-scene-mvp-ui:
	npm --prefix frontend run e2e:scene-mvp

benchmark-ai-off:
	uv run python scripts/run_png_to_shader_v1_benchmark.py --mode ai-off

benchmark-node-lab-ai-off:
	uv run python scripts/run_node_lab_benchmark.py --manifest benchmarks/node_lab/png_to_shader_v1/manifest.yaml --require-passed
	uv run python scripts/run_node_lab_benchmark.py --manifest benchmarks/node_lab/png_to_shader_v1/scenario-manifest.yaml --require-passed
	uv run python scripts/run_node_lab_benchmark.py --manifest benchmarks/node_lab/png_to_shader_v1/renderer-warm-manifest.yaml --require-passed
	uv run python scripts/run_node_lab_transport_benchmark.py --require-passed

benchmark-node-lab-model:
	uv run python scripts/run_node_lab_model_benchmark.py --execution-mode fixture --require-passed

benchmark-png-to-shader:
	uv run python scripts/run_png_to_shader_v1_benchmark.py --mode all --quality-preset $(QUALITY_PRESET) --allow-model-calls --model-call-budget $(MODEL_CALL_BUDGET)

benchmark-gate:
	@test -n "$(BENCHMARK_OUTPUT)" || (echo 'BENCHMARK_OUTPUT is required' && exit 2)
	@test -n "$(HUMAN_REVIEW)" || (echo 'HUMAN_REVIEW is required' && exit 2)
	uv run python scripts/run_png_to_shader_v1_benchmark.py --mode evaluate --output-dir "$(BENCHMARK_OUTPUT)" --human-review "$(HUMAN_REVIEW)" --require-gate-passed

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
	@echo 'dev-node-lab                 - run FastAPI with the local Node Lab API enabled'
	@echo 'dev-frontend                 - run Vite frontend'
	@echo 'check                        - run unit tests, LangGraph validation, frontend build'
	@echo 'docs-check                   - verify harness docs and architecture boundaries'
	@echo 'test-memory-postgres         - verify Shader Memory against PostgreSQL'
	@echo 'test-node-lab-ui             - verify the Node Lab workbench in isolated Chromium'
	@echo 'test-scene-mvp-ui             - verify the scene_mvp pipeline summary in isolated Chromium'
	@echo 'benchmark-ai-off             - run the 10-case renderer/oracle smoke without model calls'
	@echo 'benchmark-node-lab-ai-off    - run Node Lab capability/node/pipeline/cold/warm/transport AI-off benchmarks'
	@echo 'benchmark-node-lab-model     - run the five Node Lab model roles with offline fixtures'
	@echo 'benchmark-png-to-shader      - run the cost-gated 10-case real-model benchmark'
	@echo 'benchmark-gate               - evaluate a frozen run with HUMAN_REVIEW JSON'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'tests                        - run unit tests'
	@echo 'test TEST_FILE=<test_file>   - run all tests in file'
	@echo 'test_watch                   - run unit tests in watch mode'
