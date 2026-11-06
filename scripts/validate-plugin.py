#!/usr/bin/env python3
"""Validate the working source slice, excluding ignored builds and local databases."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).decode().split("\0")
    with tempfile.TemporaryDirectory(prefix="routing-validate-") as directory:
        staging = Path(directory)
        for name in paths:
            source = ROOT / name
            if not name or not source.is_file():
                continue
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for command in (["hermes", "plugins", "validate", str(staging)],
                        ["hermes", "plugins", "doctor", str(staging), "--ci"]):
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
