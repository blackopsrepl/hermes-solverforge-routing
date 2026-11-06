"""Public Hermes integration; no state is opened during registration."""
from pathlib import Path
from .tools import RoutingTools
from .schemas import SCHEMAS

NAME = "solverforge-routing"


def data_dir():
    from plugins.plugin_storage import plugin_data_dir
    return Path(plugin_data_dir(NAME))


def service(get_config):
    # Import lazily within the plugin package; registration never opens state.
    from .tools import core
    root = data_dir().resolve()
    binary = get_config("binary", "") or ""
    if not isinstance(binary, str):
        raise ValueError("binary must be a plain executable path")
    development = Path(__file__).parent / "engine/target/release/solverforge-routing"
    engine = Path(binary).expanduser() if binary else (development if development.is_file() else root / "bin/solverforge-routing")
    return core().RouteService(root / "routing.db", engine=[str(engine)]), engine


def register(ctx):
    tools = RoutingTools(lambda: service(ctx.get_config))
    for schema in SCHEMAS:
        ctx.register_tool(name=schema["name"], toolset=NAME, schema=schema,
                          handler=tools.handler(schema["name"]), emoji="↗")
    ctx.register_skill("routing", Path(__file__).parent / "skills/routing/SKILL.md")
