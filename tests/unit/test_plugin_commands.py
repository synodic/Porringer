"""Behavioral matrix for ``Environment`` command-construction methods.

Complements :mod:`tests.unit.test_plugin_protocol` (which only checks
shape) with cross-plugin invariants every command-builder must obey:

* Idempotent — calling twice produces equal argv (no hidden state).
* Constraint-tolerant — a constrained ``PackageRef`` does not raise.
  (Whether the constraint reaches argv is plugin-specific: some
  wrappers like brew / pyenv intentionally ignore version pins.)
* Pre-release flag is honoured deterministically (same argv for
  repeated calls with the same flag value).
* Uninstall references the package name verbatim.

Together with the protocol matrix this replaces the per-plugin
"smoke test that the install command is well-formed" duplication.
"""

from __future__ import annotations

import pytest

from porringer.backend.command.core.discovery import discover_all_plugins
from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import PackageRef


def _env_pairs() -> list[tuple[str, Environment]]:
    discovered = discover_all_plugins(use_cache=False)
    return sorted(discovered.environments.items())


_ENV = _env_pairs()
_IDS = [n for n, _ in _ENV]

_BARE = PackageRef(name='example')
_CONSTRAINED = PackageRef(name='example', constraint='>=1.2.3')


@pytest.mark.parametrize(('name', 'plugin'), _ENV, ids=_IDS)
class TestEnvironmentCommandBehaviour:
    """Per-plugin invariants for install / upgrade / uninstall command builders."""

    @staticmethod
    def test_install_is_idempotent(name: str, plugin: Environment) -> None:
        """Repeated calls to ``install_command`` with the same ref return equal argv."""
        del name
        assert plugin.install_command(_BARE) == plugin.install_command(_BARE)

    @staticmethod
    def test_upgrade_is_idempotent(name: str, plugin: Environment) -> None:
        """Repeated calls to ``upgrade_command`` with the same ref return equal argv."""
        del name
        assert plugin.upgrade_command(_BARE) == plugin.upgrade_command(_BARE)

    @staticmethod
    def test_uninstall_is_idempotent(name: str, plugin: Environment) -> None:
        """Repeated calls to ``uninstall_command`` with the same ref return equal argv."""
        del name
        assert plugin.uninstall_command(_BARE) == plugin.uninstall_command(_BARE)

    @staticmethod
    def test_install_accepts_constraint(name: str, plugin: Environment) -> None:
        """Building an install command with a constrained ref does not raise.

        Whether the constraint surfaces in argv is plugin-specific —
        some wrappers (brew, pyenv, ...) deliberately ignore version
        pins — so we only assert non-failure plus shape.
        """
        del name
        argv = plugin.install_command(_CONSTRAINED)
        assert isinstance(argv, list)
        assert argv

    @staticmethod
    def test_upgrade_accepts_constraint(name: str, plugin: Environment) -> None:
        """Building an upgrade command with a constrained ref does not raise."""
        del name
        argv = plugin.upgrade_command(_CONSTRAINED)
        assert isinstance(argv, list)
        assert argv

    @staticmethod
    def test_uninstall_references_package_name(name: str, plugin: Environment) -> None:
        """The uninstall argv must mention the package name in some form."""
        del name
        argv = plugin.uninstall_command(_BARE)
        assert any('example' in a for a in argv), f'package name missing from {argv!r}'

    @staticmethod
    def test_prerelease_flag_is_deterministic(name: str, plugin: Environment) -> None:
        """Repeated calls with the same ``include_prereleases`` value produce equal argv."""
        del name
        a = plugin.install_command(_BARE, include_prereleases=True)
        b = plugin.install_command(_BARE, include_prereleases=True)
        assert a == b
        c = plugin.install_command(_BARE, include_prereleases=False)
        d = plugin.install_command(_BARE, include_prereleases=False)
        assert c == d
