"""CLI command implementation for env."""

"""Porringer CLI environment diagnostics."""

import asyncio
import json
import os
import platform
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from porringer.api import API
from porringer.backend.schema import GlobalConfiguration
from porringer.console.output import Output
from porringer.console.schema import ConsoleConfiguration
from porringer.core.path import ensure_system_path
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Plugin
from porringer.utility.tool_environment import ENV_INFO_ENVIRONMENT_KEYS, environment_subset

app = typer.Typer(help='Inspect local environment diagnostics')


def _path_entries(raw_path: str) -> list[str]:
    """Split a PATH value into non-empty entries."""
    return [entry for entry in raw_path.split(os.pathsep) if entry]


def _path_key(path: str) -> str:
    """Return the comparison key for a PATH entry."""
    return path.lower() if os.name == 'nt' else path


def _path_summary() -> dict[str, Any]:
    """Synchronize PATH and return before/after diagnostics."""
    before = os.environ.get('PATH', '')
    before_entries = _path_entries(before)

    ensure_system_path()

    after = os.environ.get('PATH', '')
    after_entries = _path_entries(after)
    before_keys = {_path_key(entry) for entry in before_entries}
    added_entries = [entry for entry in after_entries if _path_key(entry) not in before_keys]

    return {
        'before': before,
        'after': after,
        'before_count': len(before_entries),
        'after_count': len(after_entries),
        'added_entries': added_entries,
    }


def _stringify(value: object) -> str | None:
    """Convert an optional diagnostic value to a string."""
    if value is None:
        return None
    return str(value)


def _porringer_version() -> str | None:
    """Return the installed Porringer version, if package metadata is available."""
    try:
        return version('porringer')
    except PackageNotFoundError:
        return None


def _plugin_diagnostics(name: str, plugin: Plugin, runtime_context: RuntimeContext | None) -> dict[str, Any]:
    """Collect availability diagnostics for one plugin without failing the command."""
    result: dict[str, Any] = {
        'name': name,
        'kind': plugin.plugin_kind().value,
        'ecosystem': _stringify(plugin.ecosystem()),
        'supported': None,
        'available': None,
        'tool': None,
        'which': None,
        'tool_version': None,
        'error': None,
    }

    try:
        _populate_plugin_diagnostics(result, plugin, runtime_context)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'

    return result


def _populate_plugin_diagnostics(
    result: dict[str, Any], plugin: Plugin, runtime_context: RuntimeContext | None
) -> None:
    """Fill availability diagnostics into ``result`` for one plugin."""
    result['supported'] = plugin.is_supported()
    if isinstance(plugin, ToolBasedPlugin):
        tool = plugin.tool_name()
        result['tool'] = tool
        result['which'] = shutil.which(tool) if tool is not None else None
        result['available'] = plugin.query_availability(runtime_context)
        if result['available']:
            result['tool_version'] = _stringify(plugin.tool_version())
    else:
        result['available'] = plugin.is_available()


async def _collect_plugin_info(*, use_cache: bool, resolve_runtime: bool) -> dict[str, Any]:
    """Discover plugins and collect runtime/plugin diagnostics."""
    plugins = await API.discover_plugins(use_cache=use_cache, resolve_runtime=resolve_runtime)
    runtime_context = plugins.runtime_context
    runtime = {}
    if runtime_context is not None:
        runtime = {kind: str(executable) for kind, executable in runtime_context.executables.items()}

    plugin_rows = [
        _plugin_diagnostics(name, plugin, runtime_context)
        for name, plugin in sorted(plugins.all_plugins.items(), key=lambda item: item[0])
    ]

    return {'runtime_context': runtime, 'plugins': plugin_rows}


def collect_environment_info(
    configuration: ConsoleConfiguration,
    *,
    include_plugins: bool = True,
    fresh: bool = False,
    resolve_runtime: bool = True,
) -> dict[str, Any]:
    """Collect environment diagnostics for the ``env info`` command."""
    global_configuration = GlobalConfiguration()
    info: dict[str, Any] = {
        'system': {
            'porringer_version': _porringer_version(),
            'platform': platform.platform(),
            'system': platform.system(),
            'release': platform.release(),
            'machine': platform.machine(),
            'python_version': platform.python_version(),
            'python_executable': sys.executable,
            'frozen': bool(getattr(sys, 'frozen', False)),
            'cwd': str(Path.cwd()),
        },
        'directories': {
            'cache': str(configuration.local_configuration.cache_directory),
            'config': str(global_configuration.config_directory),
            'data': str(global_configuration.data_directory),
        },
        'environment': environment_subset(ENV_INFO_ENVIRONMENT_KEYS),
        'path': _path_summary(),
        'runtime_context': {},
        'plugins': [],
    }

    if include_plugins:
        plugin_info = asyncio.run(_collect_plugin_info(use_cache=not fresh, resolve_runtime=resolve_runtime))
        info.update(plugin_info)

    return info


def _add_rows(table: Table, rows: dict[str, object]) -> None:
    """Add simple key/value rows to a table."""
    for key, value in rows.items():
        display = 'not set' if value is None else str(value)
        table.add_row(key, display)


def _print_info(output: Output, info: dict[str, Any]) -> None:
    """Render environment diagnostics as Rich tables."""
    system = Table(title='System', show_header=True, header_style='bold magenta')
    system.add_column('Field', style='cyan')
    system.add_column('Value')
    _add_rows(system, info['system'])
    output.print(system)

    directories = Table(title='Directories', show_header=True, header_style='bold magenta')
    directories.add_column('Name', style='cyan')
    directories.add_column('Path')
    _add_rows(directories, info['directories'])
    output.print(directories)

    path = info['path']
    path_table = Table(title='PATH Sync', show_header=True, header_style='bold magenta')
    path_table.add_column('Field', style='cyan')
    path_table.add_column('Value')
    path_table.add_row('entries before sync', str(path['before_count']))
    path_table.add_row('entries after sync', str(path['after_count']))
    path_table.add_row('entries added by sync', '\n'.join(path['added_entries']) or 'none')
    output.print(path_table)

    environment = Table(title='Tool Environment', show_header=True, header_style='bold magenta')
    environment.add_column('Variable', style='cyan')
    environment.add_column('Value')
    _add_rows(environment, info['environment'])
    output.print(environment)

    runtime_context = info['runtime_context']
    if runtime_context:
        runtime = Table(title='Runtime Context', show_header=True, header_style='bold magenta')
        runtime.add_column('Kind', style='cyan')
        runtime.add_column('Executable')
        _add_rows(runtime, runtime_context)
        output.print(runtime)

    if info['plugins']:
        plugins = Table(title='Plugins', show_header=True, header_style='bold magenta')
        plugins.add_column('Name', style='cyan')
        plugins.add_column('Kind')
        plugins.add_column('Tool')
        plugins.add_column('Available')
        plugins.add_column('Which')
        plugins.add_column('Version')
        plugins.add_column('Error')
        for plugin in info['plugins']:
            plugins.add_row(
                plugin['name'],
                plugin['kind'],
                plugin['tool'] or 'n/a',
                str(plugin['available']),
                plugin['which'] or 'not found',
                plugin['tool_version'] or 'n/a',
                plugin['error'] or '',
            )
        output.print(plugins)


@app.command('info')
def env_info(
    context: typer.Context,
    as_json: Annotated[
        bool,
        typer.Option('--json', help='Emit machine-readable JSON instead of tables'),
    ] = False,
    include_plugins: Annotated[
        bool,
        typer.Option('--plugins/--no-plugins', help='Include discovered plugin availability diagnostics'),
    ] = True,
    fresh: Annotated[
        bool,
        typer.Option('--fresh', help='Bypass the plugin discovery cache'),
    ] = False,
    resolve_runtime: Annotated[
        bool,
        typer.Option('--runtime/--no-runtime', help='Resolve runtime-provider context before plugin checks'),
    ] = True,
) -> None:
    """Print local environment and plugin availability diagnostics."""
    configuration = context.ensure_object(ConsoleConfiguration)
    info = collect_environment_info(
        configuration,
        include_plugins=include_plugins,
        fresh=fresh,
        resolve_runtime=resolve_runtime,
    )

    if as_json:
        typer.echo(json.dumps(info, indent=2, sort_keys=True))
        return

    _print_info(configuration.output, info)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def env_default() -> None:
    """Inspect local environment diagnostics."""
    pass
