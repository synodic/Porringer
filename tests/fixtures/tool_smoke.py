"""Real-tool dry-run acceptance helper for smoke tests."""

from __future__ import annotations

import pytest

from porringer.core.plugin_schema.environment import Environment, PackageVerb
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import PackageRef
from porringer.utility.utility import run_command


async def assert_native_dry_run_accepted(
    plugin: Environment,
    package: PackageRef,
    *,
    verb: PackageVerb = 'install',
    runtime_context: RuntimeContext | None = None,
    timeout_seconds: float = 60.0,
) -> None:
    """Assert the real tool accepts the plugin's argv in native dry-run mode.

    Builds the command from the plugin's own ``install_command`` /
    ``upgrade_command`` plus its advertised ``dry_run_flags``, then runs
    the real wrapped tool.  Unlike Porringer's ``dry_run=True`` — which
    only *resolves* the operation and never invokes the tool — this
    exercises the tool itself, so it fails loudly if the tool renames or
    drops a flag the plugin still emits.  This is the honest,
    self-maintaining counterpart to a golden-argv assertion.

    Skips when the plugin advertises no native dry-run for *verb* (an
    empty ``dry_run_flags``), since there is nothing for the tool to
    validate without mutating real state.

    Args:
        plugin: The environment plugin under test.
        package: The package reference to rehearse.
        verb: Which operation to rehearse (``install`` or ``upgrade``).
        runtime_context: Resolved runtime paths for the operation.
        timeout_seconds: Seconds to allow the rehearsal command to run.
    """
    flags = list(plugin.dry_run_flags(verb))
    if not flags:
        pytest.skip(f'{plugin.tool_name()!r} advertises no native dry-run flags for {verb!r}')
    if verb == 'upgrade':
        argv = plugin.upgrade_command(package, runtime_context=runtime_context)
    else:
        argv = plugin.install_command(package, runtime_context=runtime_context)
    result = await run_command([*argv, *flags], timeout=timeout_seconds)
    assert result.returncode == 0, f'{verb} dry-run rejected by {plugin.tool_name()!r}: {result.stderr}'
