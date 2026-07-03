"""CLI command implementation for preview.

Porringer CLI preview command.
"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from porringer.console.common import (
    EXIT_FAILURE,
    OnlyActionOption,
    PluginOption,
    ProjectDirOption,
    StrategyOption,
    TargetArgument,
    build_setup_parameters,
    classify_target,
    create_api,
    parse_shared_options,
    resolve_target_or_exit,
)
from porringer.console.schema import ConsoleConfiguration
from porringer.schema import ActionInspection, InspectionMode, SyncInspectionReport, SyncStrategy
from porringer.utility.observability import explain_inspection_report, inspection_envelope


@dataclass(slots=True)
class _PreviewOptions:
    """Bundled options for manifest preview."""

    strategy: SyncStrategy = SyncStrategy.MINIMAL
    inspection_mode: InspectionMode = InspectionMode.COMPLETE
    project_directory: Path | None = None
    plugins: set[str] | None = None
    action_ids: set[str] | None = None
    as_json: bool = False
    as_envelope: bool = False
    explain: bool = False


def _parse_inspection_mode(configuration: ConsoleConfiguration, mode: str) -> InspectionMode:
    """Parse a CLI inspection mode string."""
    try:
        return InspectionMode(mode.lower())
    except ValueError as exc:
        configuration.output.error(f"Invalid mode '{mode}'. Use: complete or fast")
        raise typer.Exit(EXIT_FAILURE) from exc


def _command_text(action: ActionInspection) -> str:
    """Return display text for an inspected action command."""
    if action.cli_command:
        return ' '.join(action.cli_command)
    return action.action.description


def _display_report(configuration: ConsoleConfiguration, report: SyncInspectionReport) -> None:
    """Render an inspection report as Rich tables."""
    output = configuration.output

    for manifest in report.manifests:
        title = str(manifest.manifest_path or '(unknown manifest)')
        output.print(f'\n[heading]Manifest:[/heading] {title}')

        for diagnostic in manifest.diagnostics:
            style = 'error' if diagnostic.severity == 'error' else 'warning'
            field = f'{diagnostic.field}: ' if diagnostic.field else ''
            output.print(f'  [{style}]{diagnostic.severity}[/{style}] {field}{diagnostic.message}')

        table = Table(show_header=True, header_style='bold magenta')
        table.add_column('#', justify='right', style='dim')
        table.add_column('Status')
        table.add_column('Action')
        table.add_column('Command')
        table.add_column('Message')

        for action in manifest.actions:
            table.add_row(
                str(action.index + 1),
                action.status.value,
                action.action.description,
                _command_text(action),
                action.message or '',
            )

        if manifest.actions:
            output.print(table)
        else:
            output.print('  [muted]No actions[/muted]')

    for failed in report.failed_paths:
        output.print(f'\n[error]Failed:[/error] {failed.path}')
        output.print(f'  [muted]{failed.error}[/muted]')

    summary = report.summary
    output.blank()
    output.print(
        Panel(
            (
                f'{summary.actions} action(s): '
                f'{summary.needed} needed, '
                f'{summary.satisfied} satisfied, '
                f'{summary.update_available} update available, '
                f'{summary.unavailable} unavailable, '
                f'{summary.failed} failed'
            ),
            border_style='green' if report.success else 'red',
        )
    )


def _emit_report(configuration: ConsoleConfiguration, report: SyncInspectionReport, options: _PreviewOptions) -> None:
    """Emit an inspection report in the requested format."""
    if options.as_envelope:
        envelope = inspection_envelope(report, operation='sync.inspect')
        typer.echo(json.dumps(envelope.model_dump(mode='json'), indent=2))
        return

    if options.as_json:
        typer.echo(json.dumps(report.model_dump(mode='json'), indent=2))
        return

    if options.explain:
        for line in explain_inspection_report(report):
            configuration.output.print(line)
        if not report.success:
            raise typer.Exit(EXIT_FAILURE)
        return

    _display_report(configuration, report)

    if not report.success:
        raise typer.Exit(EXIT_FAILURE)


def preview_profile(
    configuration: ConsoleConfiguration,
    profile_url: str,
    options: _PreviewOptions,
    *,
    expected_hash: str | None = None,
) -> None:
    """Preview a remote setup profile read-only."""
    api = create_api(configuration)
    try:
        inspection = asyncio.run(
            api.profile.inspect(
                profile_url,
                inspection_mode=options.inspection_mode,
                expected_hash=expected_hash,
            )
        )
    except ValueError as exc:
        configuration.output.error(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc

    configuration.output.print(f'[heading]Profile:[/heading] {inspection.profile.name}')
    configuration.output.print(f'[heading]Origin:[/heading] {profile_url}')
    pinned = 'pinned' if expected_hash else 'not pinned'
    configuration.output.print(f'[heading]Integrity:[/heading] HTTPS, sha256 {pinned}')

    _emit_report(configuration, inspection.inspection, options)


def preview_default(  # noqa: PLR0913
    context: typer.Context,
    target: TargetArgument = None,
    *,
    project_dir: ProjectDirOption = None,
    strategy: StrategyOption = 'minimal',
    mode: Annotated[
        str,
        typer.Option('--mode', help='Inspection mode: complete (default) or fast'),
    ] = InspectionMode.COMPLETE.value,
    plugin: PluginOption = None,
    as_json: Annotated[
        bool,
        typer.Option('--json', help='Emit machine-readable JSON'),
    ] = False,
    as_envelope: Annotated[
        bool,
        typer.Option('--envelope', help='Emit the common result envelope JSON'),
    ] = False,
    explain: Annotated[
        bool,
        typer.Option('--explain', help='Explain diagnostics and available follow-up actions'),
    ] = False,
    only_action: OnlyActionOption = None,
) -> None:
    """Preview what Porringer would do, without executing any actions."""
    configuration = context.ensure_object(ConsoleConfiguration)
    shared = parse_shared_options(
        configuration, strategy=strategy, project_dir=project_dir, plugin=plugin, only_action=only_action
    )
    options = _PreviewOptions(
        strategy=shared.strategy,
        inspection_mode=_parse_inspection_mode(configuration, mode),
        project_directory=shared.project_directory,
        plugins=shared.plugins,
        action_ids=shared.action_ids,
        as_json=as_json,
        as_envelope=as_envelope,
        explain=explain,
    )

    resolved_target = resolve_target_or_exit(configuration, target)
    api = create_api(configuration)
    plan = classify_target(configuration, api, resolved_target)

    if plan.is_profile and plan.profile_url is not None:
        preview_profile(configuration, plan.profile_url, options, expected_hash=plan.expected_hash)
        return

    params = build_setup_parameters(
        plan.manifest_paths,
        project_directory=options.project_directory,
        strategy=options.strategy,
        plugins=options.plugins,
        action_ids=options.action_ids,
        inspection_mode=options.inspection_mode,
        fail_fast=False,
    )

    try:
        report = asyncio.run(api.sync.inspect(params))
    except ValueError as exc:
        configuration.output.error(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc

    _emit_report(configuration, report, options)
