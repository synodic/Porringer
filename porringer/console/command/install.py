"""CLI command implementation for install.

Porringer CLI install command.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from porringer.console.command import sync as sync_command
from porringer.console.common import EXIT_FAILURE, create_api, parse_strategy, sniff_profile
from porringer.console.schema import ConsoleConfiguration
from porringer.core.target import TargetKind, TargetResolution, resolve_target
from porringer.schema import InspectionMode, InspectionSummary, SetupParameters, SyncStrategy


@dataclass(slots=True)
class _InstallOptions:
    """Bundled options for install execution."""

    project_directory: Path | None = None
    fail_fast: bool = True
    strategy: SyncStrategy = SyncStrategy.MINIMAL
    plugins: set[str] | None = None
    action_ids: set[str] | None = None
    all_cached: bool = False
    as_jsonl: bool = False
    record_path: Path | None = None
    yes: bool = False


def _confirm_or_exit(configuration: ConsoleConfiguration, yes: bool) -> bool:
    """Require a local confirmation unless ``--yes`` was provided."""
    if yes:
        return True

    if not configuration.console.is_terminal:
        configuration.output.error('Refusing to execute without --yes in non-interactive mode')
        raise typer.Exit(EXIT_FAILURE)

    return typer.confirm('Apply this setup plan?', default=False)


def _summary_line(prefix: str, summary: InspectionSummary) -> str:
    """Format an inspection summary as a single preview line."""
    return (
        f'{prefix}: {summary.actions} action(s), '
        f'{summary.needed} needed, '
        f'{summary.update_available} update available, '
        f'{summary.unavailable} unavailable, '
        f'{summary.failed} failed'
    )


def _setup_parameters(options: _InstallOptions, paths: Path | list[str] | None) -> SetupParameters:
    """Build setup parameters from install options."""
    return SetupParameters(
        paths=paths,
        project_directory=options.project_directory,
        fail_fast=options.fail_fast,
        strategy=options.strategy,
        plugins=options.plugins,
        action_ids=options.action_ids,
        inspection_mode=InspectionMode.FAST,
    )


def _manifest_paths(
    configuration: ConsoleConfiguration, target: TargetResolution, options: _InstallOptions
) -> Path | list[str] | None:
    """Resolve the manifest paths for a non-link install target."""
    if options.all_cached:
        return None

    if target.kind == TargetKind.PATH:
        if target.path is None or not target.path.exists():
            configuration.output.error(f'Path does not exist: {target.path}')
            raise typer.Exit(EXIT_FAILURE)
        return target.path.resolve()

    if target.kind == TargetKind.URL and target.url is not None:
        return [target.url]

    configuration.output.error(f'Unsupported install target: {target.kind}')
    raise typer.Exit(EXIT_FAILURE)


def _run_manifest_install(
    configuration: ConsoleConfiguration,
    target: TargetResolution,
    options: _InstallOptions,
) -> None:
    """Execute a manifest-oriented install target."""
    setup_params = _setup_parameters(options, _manifest_paths(configuration, target, options))

    api = create_api(configuration)

    # JSONL output must stay machine-clean, so the human preview is skipped there.
    if not options.as_jsonl:
        report = asyncio.run(api.sync.inspect(setup_params))
        configuration.output.print(_summary_line('Preview', report.summary))

    if not _confirm_or_exit(configuration, options.yes):
        configuration.output.warning('Aborted')
        return

    if options.as_jsonl or options.record_path is not None:
        execute_results = sync_command._execute_observable(
            setup_params,
            api=api,
            emit_jsonl=options.as_jsonl,
            record_path=options.record_path,
        )
    else:
        execute_results = sync_command._execute_with_progress(configuration, api, setup_params)

    if execute_results.total_actions == 0:
        if not options.as_jsonl:
            configuration.output.warning('No actions to execute')
        return

    if not options.as_jsonl:
        sync_command._display_results(configuration, execute_results, options.strategy)

    if not execute_results.success:
        raise typer.Exit(EXIT_FAILURE)


def _run_profile_install(
    configuration: ConsoleConfiguration,
    target: TargetResolution,
    options: _InstallOptions,
) -> None:
    """Execute a profile-oriented install target."""
    if target.profile_url is None:
        configuration.output.error('Install link target is missing profile URL')
        raise typer.Exit(EXIT_FAILURE)

    api = create_api(configuration)
    inspection = asyncio.run(
        api.profile.inspect(
            target.profile_url,
            inspection_mode=InspectionMode.FAST,
            expected_hash=target.expected_hash,
        )
    )
    summary = inspection.inspection.summary
    configuration.output.print(_summary_line('Profile preview', summary))

    if not _confirm_or_exit(configuration, options.yes):
        configuration.output.warning('Aborted')
        return

    execution = asyncio.run(
        api.profile.run(
            target.profile_url,
            parameters=_setup_parameters(options, None),
            expected_hash=target.expected_hash,
        )
    )

    sync_command._display_results(configuration, execution.results, options.strategy)
    if not execution.results.success:
        raise typer.Exit(EXIT_FAILURE)


def install_default(  # noqa: PLR0913
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
    fail_fast: Annotated[
        bool,
        typer.Option('--fail-fast/--no-fail-fast', help='Stop on first error'),
    ] = True,
    strategy: Annotated[
        str,
        typer.Option('--strategy', '-s', help='Install strategy: minimal (default), latest, or exact'),
    ] = 'minimal',
    plugin: Annotated[
        list[str] | None,
        typer.Option('--plugin', help='Only include actions from these plugins (repeatable).'),
    ] = None,
    only_action: Annotated[
        list[str] | None,
        typer.Option('--only-action', help='Only run a stable action id such as 0:2 (repeatable).'),
    ] = None,
    jsonl: Annotated[
        bool,
        typer.Option('--jsonl', help='Emit progress events and final result as newline-delimited JSON'),
    ] = False,
    record: Annotated[
        Path | None,
        typer.Option('--record', help='Write a replayable JSON run record to this path'),
    ] = None,
    all_cached: Annotated[
        bool,
        typer.Option('--all', '-a', help='Run on all cached directories'),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option('--yes', '-y', help='Skip confirmation prompt'),
    ] = False,
) -> None:
    """Install from the nearest manifest, an explicit path, URL, or install link."""
    configuration = context.ensure_object(ConsoleConfiguration)

    options = _InstallOptions(
        project_directory=project_dir.resolve() if project_dir else None,
        fail_fast=fail_fast,
        strategy=parse_strategy(configuration, strategy),
        plugins=set(plugin) if plugin else None,
        action_ids=set(only_action) if only_action else None,
        all_cached=all_cached,
        as_jsonl=jsonl,
        record_path=record,
        yes=yes,
    )

    if all_cached:
        _run_manifest_install(configuration, TargetResolution(kind=TargetKind.PATH), options)
        return

    try:
        resolved_target = resolve_target(target)
    except ValueError as exc:
        configuration.output.error(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc

    if resolved_target.kind == TargetKind.URL and resolved_target.url is not None:
        # A bare https URL is ambiguous; a strict profile parse decides.
        api = create_api(configuration)
        try:
            profile = asyncio.run(sniff_profile(api, resolved_target.url))
        except ValueError as exc:
            configuration.output.error(str(exc))
            raise typer.Exit(EXIT_FAILURE) from exc
        if profile is not None:
            resolved_target = TargetResolution(
                kind=TargetKind.LINK,
                profile_url=resolved_target.url,
                expected_hash=resolved_target.expected_hash,
            )

    if resolved_target.kind == TargetKind.LINK:
        _run_profile_install(configuration, resolved_target, options)
        return

    _run_manifest_install(configuration, resolved_target, options)
