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

# Tier 1: no database, no network; the network is closed in tests by the pytest-recording flag.
test-fast:
	uv run pytest -q -m "not integration and not live" --block-network

# Tier 2: no database; the network is closed.
test:
	uv run pytest -q -m "not live" --block-network

# pip-audit checks the pinned dependencies from uv.lock: the export is complete, so no
# environment is needed to resolve dependencies (--no-deps --disable-pip).
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

# Tiers 0-1, seconds.
commit-check: fmt-check lint test-fast

# Tiers 0-2: CI runs this same target on a PR.
ci-check: fmt-check lint security test

# Measurement on the frozen set: exit code 1 if an acceptance rule is broken.
eval:
	uv run python -m pii_scrub.eval

# Rebuild of the set from the seed; the result is committed before the trial run and not changed afterwards.
gen:
	uv run python tests/gen/generate.py
