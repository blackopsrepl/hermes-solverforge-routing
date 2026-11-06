#!/usr/bin/env python3
"""Fail closed before release: exact tag versions and five real pinned checksums."""
import argparse
import ast
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl",
    "x86_64-apple-darwin", "aarch64-apple-darwin", "x86_64-pc-windows-gnu",
)


def metadata():
    tree = ast.parse((ROOT / "engine_bin.py").read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            if node.target.id == "CHECKSUMS":
                values[node.target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENGINE_VERSION":
                    values[target.id] = ast.literal_eval(node.value)
    return values


def check_pins(pins, sums=None):
    if set(pins) != set(TARGETS) or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in pins.values()):
        raise ValueError("all five real engine release checksums must be pinned before a product release")
    assets = {f"solverforge-routing-{t}" + (".exe" if "windows" in t else ""): d for t, d in pins.items()}
    if sums is not None:
        published = {}
        for line in sums.splitlines():
            digest, name = line.split()
            name = name.lstrip("*")
            if name in published:
                raise ValueError("duplicate release checksum")
            published[name] = digest
        if published != assets:
            raise ValueError("pinned checksums differ from the engine release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--sums", type=Path)
    args = parser.parse_args()
    values = metadata()
    tag = args.tag
    if not re.fullmatch(r"(?:engine-)?v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", tag):
        raise ValueError("invalid release tag")
    if tag.startswith("engine-v"):
        cargo = tomllib.loads((ROOT / "engine/Cargo.toml").read_text())
        if tag.removeprefix("engine-v") != cargo["package"]["version"] or cargo["package"]["version"] != values["ENGINE_VERSION"]:
            raise ValueError("tag, Cargo and installer engine versions must match")
    else:
        match = re.search(r"^version: (\S+)$", (ROOT / "plugin.yaml").read_text(), re.M)
        if match is None:
            raise ValueError("plugin version missing")
        version = match.group(1)
        if tag != "v" + version:
            raise ValueError("tag must match plugin version")
        check_pins(values["CHECKSUMS"], args.sums.read_text() if args.sums else None)
    print(values["ENGINE_VERSION"])


if __name__ == "__main__":
    main()
