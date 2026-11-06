"""Public Hermes CLI and slash commands; downloads only on explicit install."""
import json
from .engine_bin import ENGINE_VERSION, EngineError, target_triple

USAGE = "/routing [status|install]"


def status(engine):
    binary = engine.binary()
    return {"engine": {"available": binary is not None, "path": str(binary) if binary else None,
                       "version": engine.version(), "expected_version": ENGINE_VERSION},
            "platform": target_triple() or "unsupported"}


def cli_setup(parser):
    subs = parser.add_subparsers(dest="sfr_command")
    subs.add_parser("status", help="Show the routing engine and platform")
    subs.add_parser("install", help=f"Download engine {ENGINE_VERSION} and verify its pinned checksum")


def cli_handler(engine, args):
    try:
        if (getattr(args, "sfr_command", None) or "status") == "install":
            engine.install()
        else:
            print(json.dumps(status(engine), indent=2))
        return 0
    except (EngineError, OSError) as exc:
        print(str(exc))
        return 1


def slash_handler(engine, raw=""):
    verb = (raw or "status").strip().lower()
    try:
        if verb == "status":
            return json.dumps(status(engine), indent=2)
        if verb == "install":
            messages = []
            engine.install(log=messages.append)
            return "\n".join(messages)
    except (EngineError, OSError) as exc:
        return str(exc)
    return USAGE
