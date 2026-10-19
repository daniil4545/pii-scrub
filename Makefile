# Checks: fast (format, lint, tests) and full (adds pip-audit and gitleaks).
# Needs uv and go (gitleaks).
.PHONY: fmt fmt-check lint test-fast test security commit-check ci-check eval gen

GITLEAKS := go run github.com/zricethezav/gitleaks/v8@v8.30.1
# PYSEC-2026-3447 (setuptools < 83): ignored, natasha needs setuptools<81 for pkg_resources.
PIP_AUDIT := uvx pip-audit@2.10.1 --ignore-vuln PYSEC-2026-3447

fmt:
	uv run ruff format .

fmt-check:
	uv run ruff format --check .

# Rules S (bandit) are enabled in pyproject.toml.
lint:
	uv run ruff check .

# Ступень 1: без БД и сети; сеть в тестах закрыта флагом pytest-recording.
test-fast:
	uv run pytest -q -m "not integration and not live" --block-network

# Ступень 2: без БД; сеть закрыта.
test:
	uv run pytest -q -m "not live" --block-network

# pip-audit проверяет зафиксированные зависимости из uv.lock: экспорт полный, поэтому
# окружение для разрешения зависимостей не нужно (--no-deps --disable-pip).
security:
	@set -e; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	uv export --frozen --no-hashes --no-emit-project > "$$tmp/req.txt"; \
	$(PIP_AUDIT) --strict --no-deps --disable-pip -r "$$tmp/req.txt"
	@set -e; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	root=$$(git rev-parse --show-toplevel); \
	cp "$$(git rev-parse --git-path index)" "$$tmp/index"; \
	GIT_INDEX_FILE="$$tmp/index" git -C "$$root" add -A; \
	tree=$$(GIT_INDEX_FILE="$$tmp/index" git -C "$$root" write-tree); \
	mkdir "$$tmp/tree"; git -C "$$root" archive "$$tree" | tar -x -C "$$tmp/tree"; \
	cd "$$tmp/tree" && $(GITLEAKS) dir --no-banner --redact .

# Ступени 0-1, секунды.
commit-check: fmt-check lint test-fast

# Ступени 0-2: этот же вызов делает CI на PR.
ci-check: fmt-check lint security test

# Замер на замороженном наборе: код выхода 1 при нарушении правил приёмки (docs/spec.md, раздел 7).
eval:
	uv run python -m pii_scrub.eval

# Пересборка набора из seed; результат коммитится до пробного прогона и потом не меняется.
gen:
	uv run python tests/gen/generate.py
