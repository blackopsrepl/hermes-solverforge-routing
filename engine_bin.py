"""Find, install and run the solverforge-routing engine binary.

The engine is built from ``engine/`` in this repository by its own GitHub
Actions release workflow, with a build attestation per binary. Installing
downloads the binary for this platform from that release and refuses it
unless its SHA-256 matches the checksum pinned below, so the reviewed commit
fixes exactly which binary runs. Nothing here updates itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ENGINE_VERSION = "0.2.2"
REPO = "blackopsrepl/hermes-solverforge-routing"
RELEASE_URL = f"https://github.com/{REPO}/releases/download/engine-v{ENGINE_VERSION}"
# SHA-256 of each engine-v{ENGINE_VERSION} release asset, copied from the
# release's SHA256SUMS after verifying each binary's build attestation.
CHECKSUMS: dict[str, str] = {
    "x86_64-unknown-linux-musl": "8963c6b98f67f1b59b334a47769e778464e7c01d2d7ebacdb533eb1df930bc38",
    "aarch64-unknown-linux-musl": "88835a5ae857afe9dd13fd2a0abfb7b00b1ace9e85fd23c066befa05e56803fc",
    "x86_64-apple-darwin": "7de195d8a44c246fd3b304314144def470fe677db247d2e5fa181259d19b4fff",
    "aarch64-apple-darwin": "5388415bb18f061ca494d87d63181741a9be462c74f7b37926866bc779b2692e",
    "x86_64-pc-windows-gnu": "0d81d9ab085666aa49f97d4b30b13cbf14230e32cdbe43fb15f2bd59f26f6160",
}
TARGETS = (
    "x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl",
    "x86_64-apple-darwin", "aarch64-apple-darwin", "x86_64-pc-windows-gnu",
)
MAX_BINARY_BYTES = 64 * 1024 * 1024


class EngineError(Exception):
    """A refusal or failure from the engine, carrying its machine-readable code."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail  # structured facts for the Desktop half, e.g. which calendars to pull


def target_triple() -> str | None:
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
    if arch is None:
        return None
    if sys.platform.startswith("linux"):
        return f"{arch}-unknown-linux-musl"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform == "win32" and arch == "x86_64":
        return "x86_64-pc-windows-gnu"
    return None


def asset_name(triple: str) -> str:
    return f"solverforge-routing-{triple}" + (".exe" if "windows" in triple else "")


@dataclass(frozen=True)
class Engine:
    """Locates and runs the engine. ``data_dir`` is the plugin data directory."""

    data_dir: Path
    override: str = ""
    timeout_s: int = 120

    @property
    def installed_path(self) -> Path:
        suffix = ".exe" if sys.platform == "win32" else ""
        return self.data_dir / "bin" / f"solverforge-routing-{ENGINE_VERSION}{suffix}"

    def binary(self) -> Path | None:
        """An explicit setting wins, then the pinned install, then PATH (development)."""
        if self.override:
            path = Path(os.path.expanduser(self.override))
            return path if path.is_file() and os.access(path, os.X_OK) else None
        if self.installed_path.is_file() and os.access(self.installed_path, os.X_OK):
            return self.installed_path
        # Keep existing source installs working while adopting versioned installs.
        for path in (self.data_dir / "bin" / "solverforge-routing",
                     Path(__file__).parent / "engine/target/release/solverforge-routing"):
            if path.is_file() and os.access(path, os.X_OK):
                return path
        found = shutil.which("solverforge-routing")
        return Path(found) if found else None

    def version(self) -> str | None:
        binary = self.binary()
        if binary is None:
            return None
        from .routing import engine_env
        try:
            out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10,
                                 env=engine_env())
            if out.returncode:
                return None
            envelope = json.loads(out.stdout)
            return envelope["data"]["version"] if envelope.get("status") == "ok" else None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def solve(self, problem: dict[str, Any]) -> dict[str, Any]:
        # Use the routing service's bounded streaming runner, never a second runner.
        from .routing import SubprocessEngine
        binary = self.binary()
        if binary is None:
            raise EngineError("not_installed", install_hint())
        return SubprocessEngine([str(binary)]).solve(problem)

    def install(self, log: Callable[[str], None] = print) -> Path:
        """Download, verify and install the pinned engine for this platform."""
        triple = target_triple()
        if triple is None:
            raise EngineError("unsupported_platform", f"no engine build for {sys.platform}/{platform.machine()}")
        expected = CHECKSUMS.get(triple, "")
        if not expected:
            raise EngineError("unpublished_engine", f"engine {ENGINE_VERSION} has no published {triple} build")
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise EngineError("invalid_checksum", "the reviewed engine checksum is invalid; not downloaded")
        url = f"{RELEASE_URL}/{asset_name(triple)}"
        log(f"downloading {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "hermes-solverforge-routing"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read(MAX_BINARY_BYTES + 1)
        except OSError as exc:
            raise EngineError("download_failed", str(exc)) from exc
        if len(body) > MAX_BINARY_BYTES:
            raise EngineError("bad_download", "engine download is larger than expected")
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise EngineError("checksum_mismatch", f"expected sha256 {expected}, got {actual}; not installed")
        target = self.installed_path
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".exe" if sys.platform == "win32" else "")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
            os.chmod(tmp, 0o755)
            # Check the verified artifact's actual contract before replacing an install.
            probe = Engine(self.data_dir, override=tmp).version()
            if probe != ENGINE_VERSION:
                raise EngineError("version_mismatch", f"expected engine {ENGINE_VERSION}, got {probe}; not installed")
            os.replace(tmp, target)
        finally:
            Path(tmp).unlink(missing_ok=True)
        log(f"installed {target} (sha256 {actual[:12]}…)")
        return target


def install_hint() -> str:
    return ("The SolverForge engine is not installed. Run `hermes solverforge-routing install`, "
            "type `/routing install`, or set the binary setting to a locally built engine.")
