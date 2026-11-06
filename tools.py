"""Shared JSON boundary for native tools and the plugin's own REST API."""
import importlib
import json
import os
import sqlite3
import threading
from .schemas import SCHEMAS

_LOCK = threading.RLock()
_CORE = None


def core():
    """Import the core within this package, never aliasing generic module names."""
    global _CORE
    with _LOCK:
        if _CORE is None:
            _CORE = importlib.import_module('.routing', __package__)
        return _CORE


class RoutingTools:
    def __init__(self, factory):
        self.factory = factory

    def handler(self, name):
        def handle(args=None, **_kwargs):
            return json.dumps(self.call(name, args or {}), allow_nan=False)
        return handle

    def call(self, name, args):
        try:
            schemas = {s["name"]: s for s in SCHEMAS}
            if name not in schemas:
                return self.fail("unknown_action", "Unknown routing action.")
            schema = schemas[name]["parameters"]
            if not isinstance(args, dict) or set(args) - set(schema["properties"]) or any(k not in args for k in schema["required"]):
                return self.fail("invalid_arguments", "Supply the declared fields, including expected_revision on every edit or solve.")
            action = name.removeprefix("route_")
            if action not in ("status", "current", "search_locations") and (type(args["expected_revision"]) is not int or args["expected_revision"] < 0):
                return self.fail("invalid_arguments", "expected_revision must be a nonnegative integer.")
            service, engine = self.factory()
            if action == "search_locations":
                return {"ok": True, **service.search_locations(**args)}
            available = engine.is_file() and os.access(engine, os.X_OK)
            if action in ("status", "current"):
                state = service.get_route()
            else:
                if action == "solve" and not available:
                    return self.fail("engine_missing", "Routing engine is missing or not executable. Configure the binary path; nothing was downloaded.")
                method = {"set_end": "set_endpoint", "precedence": "set_precedence"}.get(action, action)
                state = getattr(service, method)(**args)
            solution = state.get("solution")
            solved = solution.get("revision") if solution else None
            return {"ok": True, "state": {**state, "solved_revision": solved},
                    "revision": state["revision"], "solved_revision": solved,
                    "freshness": "current" if solved == state["revision"] else "pending",
                    "engine": {"available": available, "path": str(engine)},
                    "geocoder": {"url": service.locations.geocoder.url},
                    "provider": {"name": "OSRM", "url": service.provider.base_url,
                                 "disclosure": "Solve sends exact coordinates to this server. No live traffic."},
                    "estimate": "Estimated driving only", "quality": "best-found, not proven optimal"}
        except core().StaleRevision as exc:
            return self.fail("stale_revision", str(exc))
        except core().ProviderError as exc:
            return self.fail("provider_error", str(exc))
        except core().EngineError as exc:
            return self.fail("engine_error", str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            return self.fail("invalid_arguments", str(exc))
        except (OSError, sqlite3.Error) as exc:
            return self.fail("storage_error", str(exc))

    @staticmethod
    def fail(code, message):
        return {"ok": False, "error": {"code": code, "message": message}}
