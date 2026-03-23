"""Plugin implementation for Deno environment."""

import contextlib
import logging
from pathlib import Path
from typing import override

import aiohttp

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem, Package, PackageRef
from porringer.utility import HTTP_TIMEOUT


class DenoEnvironment(Environment):
    """Represents a Deno environment.

    Provides methods to install, uninstall, and upgrade global scripts
    using `deno install -g`.  Deno supports both npm (`npm:`) and
    JSR (`jsr:`) package sources.  By default, bare package names are
    prefixed with `npm:` unless they already carry a source prefix.
    """

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Deno belongs to the `deno` ecosystem."""
        return Ecosystem('deno')

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Deno wraps the `deno` CLI."""
        return 'deno'

    @staticmethod
    def _deno_specifier(package: PackageRef) -> str:
        """Build a Deno-compatible package specifier.

        Prefixes the package name with `npm:` when no source prefix
        (`npm:`, `jsr:`, `https:`) is present.  Appends the
        constraint with `@` separator if provided.
        """
        name = package.name
        if not any(name.startswith(p) for p in ('npm:', 'jsr:', 'https:', 'http:')):
            name = f'npm:{name}'
        if package.constraint:
            return f'{name}@{package.constraint}'
        return name

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to install a global script via Deno."""
        return ['deno', 'install', '-g', self._deno_specifier(package)]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to upgrade a global script via Deno."""
        return ['deno', 'install', '-g', '--force', self._deno_specifier(package)]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Returns the CLI command to uninstall a global script via Deno."""
        return ['deno', 'uninstall', '-g', package.name]

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying package registries.

        Routes packages to the appropriate registry:

        * ``jsr:`` prefixed → JSR API (``https://jsr.io/…/meta.json``)
        * ``npm:`` prefixed or bare names → shared ``_check_npm_registry``

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        logger = logging.getLogger('porringer.deno.check_updates')
        jsr_refs: list[PackageRef] = []
        npm_refs: list[PackageRef] = []

        for pkg_ref in params.packages:
            if pkg_ref.name.startswith('jsr:'):
                jsr_refs.append(pkg_ref)
            else:
                # Strip npm: prefix for the registry lookup, but keep
                # the original name in the result.
                npm_refs.append(pkg_ref)

        # Delegate npm-compatible packages to the shared helper
        results = await self._check_npm_registry(
            [PackageRef.model_validate(r.name[4:] if r.name.startswith('npm:') else r.name) for r in npm_refs],
            include_prereleases=params.include_prereleases,
            logger=logger,
            http_client=params.http_client,
        )
        # Restore original names (with npm: prefix) for npm results
        for i, npm_ref in enumerate(npm_refs):
            if i < len(results):
                results[i] = Package(name=npm_ref.name, version=results[i].version)

        # JSR packages
        if jsr_refs:
            async with (
                contextlib.nullcontext(params.http_client)
                if params.http_client is not None
                else aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
            ) as session:
                for pkg_ref in jsr_refs:
                    jsr_name = pkg_ref.name[4:]  # strip 'jsr:'
                    try:
                        async with session.get(f'https://jsr.io/{jsr_name}/meta.json') as response:
                            response.raise_for_status()
                            data = await response.json()
                            latest = data.get('latest')
                            if latest:
                                results.append(Package(name=pkg_ref.name, version=latest))
                    except (aiohttp.ClientError, ValueError) as exc:
                        logger.debug('JSR query failed for %s: %s', pkg_ref.name, exc)

        return results

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        """Gathers globally installed Deno scripts.

        Deno does not provide a structured list of globally installed
        scripts; returns an empty list.  *project_path* is accepted
        for interface compatibility.

        Args:
            project_path: Unused.
            runtime_context: Unused.  Deno is not Python-scoped.

        Returns:
            An empty list.
        """
        return []
