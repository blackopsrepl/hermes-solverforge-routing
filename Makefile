SHELL := /bin/bash
.DEFAULT_GOAL := help
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
CARGO ?= cargo
CARGO_TARGET_DIR ?= $(ROOT)engine/target
export CARGO_TARGET_DIR
ENGINE_BIN := $(abspath $(CARGO_TARGET_DIR))/release/solverforge-routing
PYTEST := env -u PYTHONPATH -u PYTHONHOME uv run --no-project --python 3.12 --with pytest --with fastapi --with httpx --with pyyaml --with opencv-python-headless --with numpy pytest --import-mode=importlib

.PHONY: help engine test test-engine test-plugin test-desktop lint validate check
help:
	@printf '\033[32mSolverForge Routing\033[0m · personal road itineraries\n\n'
	@printf '  make engine        Build the Rust route optimiser\n  make check         Format, lint, engine, plugin, Desktop and catalog gates\n  make install-dev   Install this checkout with its locally built engine\n  make dist          Cross-build one target: TARGET=<triple>\n  make dist-all      Cross-build all five release targets\n  make apple-sdk     Prepare the pinned SDK for Apple cross-builds\n  make version       Show plugin and engine versions\n  make images        Render README banner and architecture images\n\n'
	@printf 'CARGO_TARGET_DIR can point outside this checkout.\n'
engine:
	$(CARGO) build --manifest-path "$(ROOT)engine/Cargo.toml" --release --locked
lint:
	$(CARGO) fmt --manifest-path "$(ROOT)engine/Cargo.toml" --check
	$(CARGO) clippy --manifest-path "$(ROOT)engine/Cargo.toml" --locked --all-targets -- -D warnings
test-engine:
	$(CARGO) test --manifest-path "$(ROOT)engine/Cargo.toml" --locked
test-plugin: engine
	cd "$(ROOT)" && $(PYTEST) tests -q
test-desktop:
	npm --prefix "$(ROOT)tests/desktop" ci --ignore-scripts
	npm --prefix "$(ROOT)tests/desktop" test
test: test-engine test-plugin test-desktop
validate:
	python3 scripts/validate-plugin.py
check: lint test validate
	@printf '\033[32m✓ All checks passed\033[0m\n'

# The same five Linux-cross-built artifacts as Planner; no macOS runners.
.PHONY: install-dev uninstall-dev fmt
install-dev: engine
	hermes plugins install "file://$(CURDIR)" --force --enable --yes-deps --ref "$$(git -C "$(CURDIR)" rev-parse HEAD)"
	hermes config set plugins.entries.solverforge-routing.settings.binary "$(ENGINE_BIN)"
uninstall-dev:
	hermes plugins remove solverforge-routing
fmt:
	$(CARGO) fmt --manifest-path "$(ROOT)engine/Cargo.toml"
TARGETS := x86_64-unknown-linux-musl aarch64-unknown-linux-musl x86_64-apple-darwin aarch64-apple-darwin x86_64-pc-windows-gnu
.PHONY: dist dist-all version images apple-sdk
apple-sdk:
	scripts/prepare-apple-sdk.sh
dist:
	@test -n "$(TARGET)" || { printf 'Pass TARGET=<triple>\n'; exit 2; }
	scripts/build-engine.sh "$(TARGET)"
dist-all:
	@for target in $(TARGETS); do scripts/build-engine.sh "$$target" || exit $$?; done
	cd "$(ROOT)dist" && sha256sum $(addprefix solverforge-routing-,$(filter-out x86_64-pc-windows-gnu,$(TARGETS))) solverforge-routing-x86_64-pc-windows-gnu.exe > SHA256SUMS
version:
	@python3 -c 'import pathlib,re,tomllib; print("plugin",re.search(r"^version: (.*)$$",pathlib.Path("plugin.yaml").read_text(),re.M)[1]); print("engine",tomllib.loads(pathlib.Path("engine/Cargo.toml").read_text())["package"]["version"])'
images:
	env -u PYTHONPATH -u PYTHONHOME uv run --no-project --python 3.12 --with playwright --with pillow python scripts/render-images.py
.PHONY: screenshots
screenshots: engine
	npm --prefix "$(ROOT)tests/desktop" ci --ignore-scripts
	env -u PYTHONPATH -u PYTHONHOME uv run --no-project --python 3.12 --with playwright --with fastapi --with uvicorn python scripts/capture-screenshots.py --engine "$(ENGINE_BIN)"
