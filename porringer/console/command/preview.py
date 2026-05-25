"""CLI command implementation for preview."""

"""Porringer CLI preview command."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from porringer.console.common import EXIT_FAILURE, create_api, parse_strategy, sniff_profile
from porringer.console.schema import ConsoleConfiguration
from porringer.core.target import TargetKind, TargetResolution, resolve_target
from porringer.schema import ActionInspection, InspectionMode, SetupParameters, SyncInspectionReport, SyncStrategy
from porringer.utility.observability import explain_inspection_report, inspection_envelope


@dataclass(slots=True)
class _PreviewOptions:
    """Bundled options for manifest preview."""

    all_cached: bool = False
    fail_fast: bool = True
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


def _setup_parameters(
    configuration: ConsoleConfiguration,
    options: _PreviewOptions,
    target: TargetResolution | None,
) -> SetupParameters:
    """Build setup parameters from preview options."""
    path_value: Path | None
    if options.all_cached:
        path_value = None
    elif target is not None and target.path is not None:
        if not target.path.exists():
            configuration.output.error(f'Path does not exist: {target.path}')
            raise typer.Exit(EXIT_FAILURE)
        path_value = target.path.resolve()
    else:
        path_value = Path('.').resolve()

    paths: Path | list[str] | None = path_value
    if target is not None and target.kind == TargetKind.URL and target.url is not None:
        paths = [target.url]

    return SetupParameters(
        paths=paths,
        project_directory=options.project_directory,
        fail_fast=options.fail_fast,
        strategy=options.strategy,
        inspection_mode=options.inspection_mode,
        plugins=options.plugins,
        action_ids=options.action_ids,
    )


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
    target: Annotated[
        str | None,
        typer.Argument(help='Optional target: local path, https URL, or porringer:// install link'),
    ] = None,
    *,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            '--project-dir',
            '-d',
            help='Working directory for project-sync actions',
        ),
    ] = None,
    all_cached: Annotated[
        bool,
        typer.Option('--all', '-a', help='Preview all cached directories'),
    ] = False,
    fail_fast: Annotated[
        bool,
        typer.Option('--fail-fast/--no-fail-fast', help='Stop on first manifest load error'),
    ] = True,
    strategy: Annotated[
        str,
        typer.Option('--strategy', '-s', help='Install strategy: minimal (default), latest, or exact'),
    ] = 'minimal',
    mode: Annotated[
        str,
        typer.Option('--mode', help='Inspection mode: complete (default) or fast'),
    ] = InspectionMode.COMPLETE.value,
    plugin: Annotated[
        list[str] | None,
        typer.Option('--plugin', help='Only include actions from these plugins (repeatable).'),
    ] = None,
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
    only_action: Annotated[
        list[str] | None,
        typer.Option('--only-action', help='Only include a stable action id such as 0:2 (repeatable).'),
    ] = None,
) -> None:
    """Preview what Porringer would do, without executing any actions."""
    configuration = context.ensure_object(ConsoleConfiguration)
    options = _PreviewOptions(
        all_cached=all_cached,
        fail_fast=fail_fast,
        strategy=parse_strategy(configuration, strategy),
        inspection_mode=_parse_inspection_mode(configuration, mode),
        project_directory=project_dir.resolve() if project_dir else None,
        plugins=set(plugin) if plugin else None,
        action_ids=set(only_action) if only_action else None,
        as_json=as_json,
        as_envelope=as_envelope,
        explain=explain,
    )

    resolved_target: TargetResolution | None = None
    if not all_cached:
        try:
            resolved_target = resolve_target(target)
        except ValueError as exc:
            configuration.output.error(str(exc))
            raise typer.Exit(EXIT_FAILURE) from exc

    if resolved_target is not None and resolved_target.kind == TargetKind.URL and resolved_target.url is not None:
        api = create_api(configuration)
        try:
            profile = asyncio.run(sniff_profile(api, resolved_target.url))
        except ValueError as exc:
            configuration.output.error(str(exc))
            raise typer.Exit(EXIT_FAILURE) from exc
        if profile is not None:
            preview_profile(configuration, resolved_target.url, options)
            return

    if resolved_target is not None and resolved_target.kind == TargetKind.LINK:
        if resolved_target.profile_url is None:
            configuration.output.error('Install link target is missing profile URL')
            raise typer.Exit(EXIT_FAILURE)
        preview_profile(
            configuration,
            resolved_target.profile_url,
            options,
            expected_hash=resolved_target.expected_hash,
        )
        return

    api = create_api(configuration)
    params = _setup_parameters(configuration, options, resolved_target)

    try:
        report = asyncio.run(api.sync.inspect(params))
    except ValueError as exc:
        configuration.output.error(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc

    _emit_report(configuration, report, options)
