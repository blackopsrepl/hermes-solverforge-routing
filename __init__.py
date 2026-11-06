"""Public Hermes integration; no state is opened during registration."""
from pathlib import Path
from .tools import RoutingTools
from .schemas import SCHEMAS

NAME = "solverforge-routing"


def data_dir():
    from plugins.plugin_storage import plugin_data_dir
    return Path(plugin_data_dir(NAME))


def engine(get_config):
    from .engine_bin import Engine
    binary = get_config("binary", "") or ""
    if not isinstance(binary, str):
        raise ValueError("binary must be a plain executable path")
    return Engine(data_dir().resolve(), override=binary)


def service(get_config):
    # Import lazily within the plugin package; registration never opens state.
    from .tools import core
    root = data_dir().resolve()
    manager = engine(get_config)
    executable = manager.binary() or (Path(manager.override).expanduser() if manager.override else manager.installed_path)
    from .geocoding import NominatimGeocoder, DEFAULT_URL
    geocoder = NominatimGeocoder(get_config("geocoder_url", DEFAULT_URL))
    return core().RouteService(root / "routing.db", engine=[str(executable)], geocoder=geocoder), executable


def register(ctx):
    tools = RoutingTools(lambda: service(ctx.get_config))
    for schema in SCHEMAS:
        ctx.register_tool(name=schema["name"], toolset=NAME, schema=schema,
                          handler=tools.handler(schema["name"]), emoji="↗")
    from .commands import cli_handler, cli_setup, slash_handler
    ctx.register_command(
        "routing", lambda raw="", **kw: slash_handler(engine(ctx.get_config), raw),
        description="SolverForge Routing: engine status and install",
        args_hint="[status|install]",
    )
    ctx.register_cli_command(
        name=NAME, help="Install and check the SolverForge routing engine",
        setup_fn=cli_setup,
        handler_fn=lambda args: cli_handler(engine(ctx.get_config), args),
    )
    ctx.register_skill("routing", Path(__file__).parent / "skills/routing/SKILL.md")
