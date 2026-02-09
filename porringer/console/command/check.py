"""Porringer CLI check command module for checking package updates via plugins."""

import logging
from typing import Annotated

import typer
from packaging.version import Version
from rich.panel import Panel
from rich.table import Table

from porringer.backend.builder import Builder
from porringer.console.schema import Configuration
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.schema import (
    CheckParameters,
    CheckResult,
    PackageUpdateInfo,
)
from porringer.utility.exception import PluginError, UpdateError

app = typer.Typer()


def _check_plugin_updates(
    configuration: Configuration,
    params: CheckParameters,
) -> list[CheckResult]:
    """Check for updates across all plugins.

    Args:
        configuration: CLI configuration.
        params: Check parameters.

    Returns:
        List of check results per plugin.
    """
    logger = logging.getLogger('porringer')

    environment_types = Builder.find_plugins('environment', Environment, check_dependencies=True)
    environments = Builder.build_plugins(environment_types)

    results: list[CheckResult] = []

    for env in environments:
        plugin_name = type(env).__name__

        # Skip if specific plugins requested and this isn't one
        if params.plugins and plugin_name not in params.plugins:
            continue

        try:
            check_params = CheckUpdatesParameters(
                packages=[],  # Check all packages
                include_prereleases=params.include_prereleases,
            )

            # Get currently installed packages
            installed = env.packages()
            installed_map = {str(p.name): p for p in installed}

            # Check for updates
            updates = env.check_updates(check_params)

            # Build package update info
            package_infos: list[PackageUpdateInfo] = []
            for update_pkg in updates:
                current = installed_map.get(str(update_pkg.name))
                current_version = Version(current.version) if current and current.version else None
                latest_version = Version(update_pkg.version) if update_pkg.version else None
                package_infos.append(
                    PackageUpdateInfo(
                        name=str(update_pkg.name),
                        current_version=current_version,
                        latest_version=latest_version,
                        update_available=True,
                    )
                )

            results.append(CheckResult(plugin=plugin_name, packages=package_infos))

        except PluginError as e:
            logger.error(f'Plugin error checking updates for {plugin_name}: {e}')
            results.append(CheckResult(plugin=plugin_name, error=str(e)))
        except UpdateError as e:
            logger.error(f'Update check error for {plugin_name}: {e}')
            results.append(CheckResult(plugin=plugin_name, error=str(e)))
        except Exception as e:
            logger.warning(f'Failed to check updates for {plugin_name}: {e}')
            results.append(CheckResult(plugin=plugin_name, error=str(e)))

    return results


def _display_results(configuration: Configuration, results: list[CheckResult]) -> None:
    """Display check results to the console.

    Args:
        configuration: CLI configuration.
        results: List of check results.
    """
    total_updates = sum(r.updates_available for r in results if r.success)

    if total_updates == 0:
        configuration.console.print(Panel('[dim]All packages are up to date.[/dim]', border_style='dim'))
        return

    configuration.console.print(f'\n[bold green]{total_updates} update(s) available[/bold green]\n')

    for result in results:
        if not result.success:
            configuration.console.print(f'[yellow]{result.plugin}:[/yellow] [red]Error: {result.error}[/red]')
            continue

        if not result.packages:
            continue

        table = Table(title=f'[bold]{result.plugin}[/bold]', show_header=True)
        table.add_column('Package', style='cyan')
        table.add_column('Current', style='dim')
        table.add_column('Latest', style='green')

        for pkg in result.packages:
            table.add_row(
                pkg.name,
                str(pkg.current_version) if pkg.current_version else 'N/A',
                str(pkg.latest_version) if pkg.latest_version else 'N/A',
            )

        configuration.console.print(table)
        configuration.console.print()


@app.callback(invoke_without_command=True)
def check_default(
    context: typer.Context,
    *,
    plugin: Annotated[
        list[str] | None,
        typer.Option('--plugin', '-p', help='Plugin(s) to check. Omit to check all.'),
    ] = None,
    include_prereleases: Annotated[
        bool,
        typer.Option('--prereleases', help='Include pre-release versions'),
    ] = False,
) -> None:
    """Check for available package updates via plugins.

    Each plugin uses its native tooling to check for updates.

    Examples:
        porringer check
        porringer check --plugin pip
        porringer check --plugin pip --plugin pipx --prereleases
    """
    configuration = context.ensure_object(Configuration)

    params = CheckParameters(
        plugins=plugin,
        include_prereleases=include_prereleases,
    )

    results = _check_plugin_updates(configuration, params)
    _display_results(configuration, results)
