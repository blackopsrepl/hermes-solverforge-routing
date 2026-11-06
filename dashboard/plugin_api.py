"""Own REST namespace, mounted by Hermes under /api/plugins/solverforge-routing."""
import importlib.util
from pathlib import Path
import sys
import threading
from fastapi import APIRouter, Body
from typing import Any
from fastapi.responses import JSONResponse

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "hermes_solverforge_routing_dashboard_pkg"
_LOCK = threading.Lock()
_LOADED = []


def _package():
    with _LOCK:
        if not _LOADED:
            spec = importlib.util.spec_from_file_location(_PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PACKAGE] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(_PACKAGE, None)
                raise
            _LOADED.append(module)
        return _LOADED[0]


def _config(key, default=None):
    from hermes_cli.config import load_config_readonly
    entries = (load_config_readonly().get("plugins") or {}).get("entries") or {}
    return ((entries.get("solverforge-routing") or {}).get("settings") or {}).get(key, default)


def _call(action, body):
    package = _package()
    result = package.RoutingTools(lambda: package.service(_config)).call("route_" + action, body)
    if action == "current" and result["ok"]:
        from importlib import import_module
        result["map_document"] = import_module(".map_document", package.__name__).map_document(result["state"])
    code = 200 if result["ok"] else {"stale_revision": 409, "engine_missing": 503, "provider_error": 502, "engine_error": 502, "storage_error": 503}.get(result["error"]["code"], 400)
    return JSONResponse(result, status_code=code)


router = APIRouter()


@router.get("/status")
def status():
    return _call("status", {})


@router.get("/current")
def current():
    return _call("current", {})


@router.post("/actions/{action}")
def mutate(action: str, body: Any = Body(default=None)):
    # No gateway agent requests or sessions: a deliberate UI action calls only
    # this plugin's own revision-checked service.
    if action in ("status", "current"):
        return JSONResponse({"ok": False, "error": {"code": "unknown_action", "message": "Use the read endpoint."}}, status_code=400)
    return _call(action, body)
