SHELL := /bin/bash
.DEFAULT_GOAL := help
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
CARGO ?= cargo
CARGO_TARGET_DIR ?= $(ROOT)engine/target
export CARGO_TARGET_DIR
ROUTING_ENGINE ?= $(CARGO_TARGET_DIR)/release/solverforge-routing
export ROUTING_ENGINE
PYTEST := uv run --no-project --python 3.12 --with pytest --with fastapi --with httpx --with pyyaml --with opencv-python-headless --with numpy pytest --import-mode=importlib

.PHONY: help engine test test-engine test-plugin test-desktop lint validate check live
help:
	@printf '\033[32mSolverForge Routing\033[0m · personal road itineraries\n\n'
	@printf '  make engine        Build the Rust route optimiser\n  make check         Format, lint, engine, plugin, Desktop and catalog gates\n  make live          Real OSRM + Rust route, then add a stop and repair\n\n'
	@printf 'CARGO_TARGET_DIR and ROUTING_ENGINE can point outside this checkout.\n'
engine:
	$(CARGO) build --manifest-path "$(ROOT)engine/Cargo.toml" --release --locked
lint:
	$(CARGO) fmt --manifest-path "$(ROOT)engine/Cargo.toml" --check
	$(CARGO) clippy --manifest-path "$(ROOT)engine/Cargo.toml" --locked --all-targets -- -D warnings
test-engine:
	$(CARGO) test --manifest-path "$(ROOT)engine/Cargo.toml" --locked
test-plugin: engine
	cd "$(ROOT)" && PYTHONPATH="$(ROOT)" $(PYTEST) tests -q
test-desktop:
	npm --prefix "$(ROOT)tests/desktop" ci --ignore-scripts
	npm --prefix "$(ROOT)tests/desktop" test
test: test-engine test-plugin test-desktop
validate:
	hermes plugins validate "$(ROOT)"
	hermes plugins doctor "$(ROOT)" --ci
check: lint test validate
	@printf '\033[32m✓ All checks passed\033[0m\n'
live: engine
	@test -n "$$TMPDIR" || { printf 'Set TMPDIR to your scratch directory\n'; exit 1; }
	cd "$(ROOT)" && python3 tests/live_smoke.py --engine "$(ROUTING_ENGINE)" --evidence "$$TMPDIR/routing-live-evidence.json"
