"""Installer refusals and public commands; version probes use the real artifact."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALIAS = "routing_install_test"
spec = importlib.util.spec_from_file_location(ALIAS, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[ALIAS] = package
spec.loader.exec_module(package)
from routing_install_test import commands, engine_bin


def artifact():
    return Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "engine/target")) / "release/solverforge-routing"


def test_actual_artifact_version_and_no_stdin_network(tmp_path):
    binary = artifact()
    assert binary.is_file(), "make test-plugin must build the real engine"
    proc = subprocess.run([str(binary), "--version"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"status": "ok", "data": {"version": engine_bin.ENGINE_VERSION}}
    assert engine_bin.Engine(tmp_path, override=str(binary)).version() == engine_bin.ENGINE_VERSION


def test_empty_or_invalid_pin_refuses_before_network(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(engine_bin, "target_triple", lambda: engine_bin.TARGETS[0])
    for pins, code in [({}, "unpublished_engine"), ({engine_bin.TARGETS[0]: "not-a-sha256"}, "invalid_checksum")]:
        monkeypatch.setattr(engine_bin, "CHECKSUMS", pins)
        with pytest.raises(engine_bin.EngineError) as error:
            engine_bin.Engine(tmp_path).install(log=lambda _: None)
        assert error.value.code == code
    assert calls == [] and not (tmp_path / "bin").exists()


def test_checksum_mismatch_preserves_existing_install(monkeypatch, tmp_path):
    triple = engine_bin.TARGETS[0]
    monkeypatch.setattr(engine_bin, "target_triple", lambda: triple)
    # A real binary, not a fabricated executable; the incorrect digest is deliberate.
    body = artifact().read_bytes()
    monkeypatch.setattr(engine_bin, "CHECKSUMS", {triple: "0" * 64})
    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    engine = engine_bin.Engine(tmp_path)
    engine.installed_path.parent.mkdir()
    engine.installed_path.write_bytes(body)
    with pytest.raises(engine_bin.EngineError, match="checksum_mismatch"):
        engine.install(log=lambda _: None)
    assert engine.installed_path.read_bytes() == body


def test_verified_real_binary_atomic_install(monkeypatch, tmp_path):
    triple = engine_bin.TARGETS[0]
    body = artifact().read_bytes()
    # Test-only digest of the local build, never shipped as a release pin.
    monkeypatch.setattr(engine_bin, "target_triple", lambda: triple)
    monkeypatch.setattr(engine_bin, "CHECKSUMS", {triple: hashlib.sha256(body).hexdigest()})
    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    engine = engine_bin.Engine(tmp_path)
    installed = engine.install(log=lambda _: None)
    assert installed.read_bytes() == body
    assert engine.version() == engine_bin.ENGINE_VERSION
    assert list(installed.parent.iterdir()) == [installed]


@pytest.mark.parametrize("system,machine,target", [
    ("linux", "amd64", "x86_64-unknown-linux-musl"),
    ("linux", "aarch64", "aarch64-unknown-linux-musl"),
    ("darwin", "arm64", "aarch64-apple-darwin"),
    ("darwin", "x86_64", "x86_64-apple-darwin"),
    ("win32", "AMD64", "x86_64-pc-windows-gnu"),
    ("win32", "arm64", None), ("linux", "riscv64", None),
])
def test_platform_selection(monkeypatch, system, machine, target):
    monkeypatch.setattr(engine_bin.sys, "platform", system)
    monkeypatch.setattr(engine_bin.platform, "machine", lambda: machine)
    assert engine_bin.target_triple() == target


def test_version_mismatch_cleans_staging_and_preserves_install(monkeypatch, tmp_path):
    body = artifact().read_bytes()
    monkeypatch.setattr(engine_bin, "target_triple", lambda: engine_bin.TARGETS[0])
    monkeypatch.setattr(engine_bin, "CHECKSUMS", {engine_bin.TARGETS[0]: hashlib.sha256(body).hexdigest()})
    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    # Ask the installer for a different version; the real binary must refuse it.
    monkeypatch.setattr(engine_bin, "ENGINE_VERSION", "0.0.0")
    engine = engine_bin.Engine(tmp_path)
    engine.installed_path.parent.mkdir()
    engine.installed_path.write_bytes(body)
    with pytest.raises(engine_bin.EngineError, match="version_mismatch"):
        engine.install(log=lambda _: None)
    assert engine.installed_path.read_bytes() == body
    assert list(engine.installed_path.parent.iterdir()) == [engine.installed_path]


def test_unsupported_platform_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_bin, "target_triple", lambda: None)
    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unsupported platform downloaded"))
    with pytest.raises(engine_bin.EngineError, match="unsupported_platform"):
        engine_bin.Engine(tmp_path).install()


def test_public_commands_and_profile_resolution(monkeypatch, tmp_path, capsys):
    parser = argparse.ArgumentParser()
    commands.cli_setup(parser)
    manager = engine_bin.Engine(tmp_path, override=str(artifact()))
    assert commands.cli_handler(manager, parser.parse_args(["status"])) == 0
    assert json.loads(capsys.readouterr().out)["engine"]["version"] == engine_bin.ENGINE_VERSION
    monkeypatch.setattr(engine_bin, "CHECKSUMS", {})
    assert commands.cli_handler(manager, parser.parse_args(["install"])) == 1
    assert "unpublished_engine" in capsys.readouterr().out
    assert "unpublished_engine" in commands.slash_handler(manager, "install")
    current = [tmp_path / "one"]
    monkeypatch.setattr(package, "data_dir", lambda: current[0])
    class Context:
        get_config = staticmethod(lambda key, default=None: default)
        def register_tool(self, **kwargs): pass
        def register_skill(self, *args): pass
        def register_command(self, name, handler, **kwargs): self.slash = handler
        def register_cli_command(self, **kwargs): self.cli = kwargs
    ctx = Context()
    package.register(ctx)
    assert ctx.cli["name"] == "solverforge-routing"
    assert package.engine(ctx.get_config).data_dir == current[0]
    current[0] = tmp_path / "two"
    assert package.engine(ctx.get_config).data_dir == current[0]
    assert not current[0].exists(), "registration and engine resolution do not open route state"


def test_release_checks_refuse_empty_and_changed_pins():
    spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/check-release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with pytest.raises(ValueError, match="five real"):
        release.check_pins({})
    digest = hashlib.sha256(artifact().read_bytes()).hexdigest()
    pins = {target: digest for target in engine_bin.TARGETS}
    sums = "\n".join(f"{digest}  {engine_bin.asset_name(t)}" for t in engine_bin.TARGETS)
    release.check_pins(pins, sums)
    with pytest.raises(ValueError, match="differ"):
        release.check_pins(pins, sums.replace(digest, "0" * 64, 1))


@pytest.mark.parametrize("change", [
    {"solve_seconds": 0}, {"origin": 1}, {"destination": 4},
    {"max_snap_m": -1}, {"stop_ids": ["same", "same"]},
    {"points": [[181, 0], [0, 0]]}, {"precedence": [[1, 1]]},
])
def test_bad_requests_refused_before_roads(tmp_path, change):
    request = {"points": [[9, 45], [9.01, 45]], "stop_ids": ["origin", "stop"],
               "origin": 0, "round_trip": False, "solve_seconds": 1,
               "max_snap_m": 250, "cache_dir": str(tmp_path / "roads"), **change}
    proc = subprocess.run([str(artifact())], input=json.dumps(request), capture_output=True,
                          text=True, timeout=5)
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] in ("invalid_input", "impossible_constraints")
    assert not (tmp_path / "roads").exists()
