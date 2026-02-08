"""Plugin implementation for Homebrew (brew) package manager."""

import json
import logging
import subprocess
from typing import override

from porringer.core.plugin_schema.environment import (
    Environment,
    PackageParameters,
    UninstallParameters,
)
from porringer.core.schema import Package, PackageRef


class BrewEnvironment(Environment):
    """Represents a macOS/Linux environment managed by Homebrew (brew).

    Provides methods to install, search, uninstall, upgrade, and list packages using
    Homebrew as the backend package manager.

    This plugin is available on macOS (darwin) and Linux where Homebrew is installed.

    This plugin provides the "python-runtime" capability, which pip and pipx can use
    to manage Python versions via formulas like python@3.12.

    CLI Reference:
        - brew list [--formula] [--json]  - List installed formulas
        - brew install <formula>  - Install a formula
        - brew uninstall <formula>  - Uninstall a formula
        - brew upgrade <formula>  - Upgrade a formula
        - brew info <formula> --json=v2  - Get formula info in JSON format
        - brew search <text>  - Search for formulas
    """

    @staticmethod
    @override
    def package_backend() -> str:
        """Homebrew manages the ``system`` package backend."""
        return 'system'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Homebrew wraps the ``brew`` CLI."""
        return 'brew'

    @override
    def install_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to install a package via brew."""
        return ['brew', 'install', package.name]

    @override
    def upgrade_command(self, package: PackageRef) -> list[str]:
        """Returns the CLI command to upgrade a package via brew."""
        return ['brew', 'upgrade', package.name]

    @override
    def install(self, params: PackageParameters) -> Package | None:
        """Installs a package using Homebrew.

        Args:
            params: Installation parameters containing the formula name

        Returns:
            The installed package, or None if installation failed
        """
        logger = logging.getLogger('porringer.brew.install')

        formula = params.package.name
        args = ['brew', 'install', formula]

        if params.dry:
            args.append('--dry-run')
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=params.package.name, version=None)

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('Homebrew (brew) not found. Install it from https://brew.sh')
            return None
        except Exception as e:
            logger.error(f'Failed to install {formula}: {e}')
            return None

        # Try to get the installed version
        version = self.__class__._get_formula_version(formula)
        return Package(name=params.package.name, version=version)

    @override
    def search(self, package: PackageRef) -> Package | None:
        """Searches for a formula in Homebrew.

        Args:
            package: The package reference to search for

        Returns:
            The package if found, or None if it doesn't exist
        """
        logger = logging.getLogger('porringer.brew.search')
        formula = package.name

        try:
            # Use brew info with JSON output to check if formula exists
            result = subprocess.run(
                ['brew', 'info', formula, '--json=v2'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f'Formula {formula} not found')
                return None

            # Parse JSON output
            info = json.loads(result.stdout) if result.stdout.strip() else {}
            formulas = info.get('formulae', [])
            if formulas:
                formula_info = formulas[0]
                version = formula_info.get('versions', {}).get('stable', 'unknown')
                return Package(name=package.name, version=version)

        except FileNotFoundError:
            logger.error('Homebrew (brew) not found')
        except json.JSONDecodeError:
            logger.warning(f'Could not parse info for {formula}')
        except Exception as e:
            logger.error(f'Failed to search for {formula}: {e}')

        return None

    @override
    def uninstall(self, params: UninstallParameters) -> list[Package | None]:
        """Uninstalls formulas from Homebrew.

        Args:
            params: Uninstall parameters containing the list of formula names

        Returns:
            A list of uninstalled packages, with None for any that failed
        """
        logger = logging.getLogger('porringer.brew.uninstall')
        results: list[Package | None] = []

        for pkg in params.packages:
            formula = pkg.name
            args = ['brew', 'uninstall', formula]

            if params.dry:
                args.append('--dry-run')
                logger.info(f'[dry-run] Would run: {" ".join(args)}')
                results.append(Package(name=pkg.name, version=None))
                continue

            try:
                result = subprocess.run(args, capture_output=True, text=True, check=False)
                logger.info(result.stdout)
                if result.returncode == 0:
                    results.append(Package(name=pkg.name, version=None))
                else:
                    logger.error(result.stderr)
                    results.append(None)
            except FileNotFoundError:
                logger.error('Homebrew (brew) not found')
                results.append(None)
            except Exception as e:
                logger.error(f'Failed to uninstall {formula}: {e}')
                results.append(None)

        return results

    @override
    def upgrade(self, params: PackageParameters) -> Package | None:
        """Upgrades a formula in Homebrew.

        Args:
            params: Upgrade parameters containing the formula name

        Returns:
            The upgraded package, or None if the upgrade failed
        """
        logger = logging.getLogger('porringer.brew.upgrade')
        pkg = params.package
        formula = pkg.name
        args = ['brew', 'upgrade', formula]

        if params.dry:
            args.append('--dry-run')
            logger.info(f'[dry-run] Would run: {" ".join(args)}')
            return Package(name=pkg.name, version=None)

        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            logger.info(result.stdout)
            if result.returncode != 0:
                logger.error(result.stderr)
                return None
        except FileNotFoundError:
            logger.error('Homebrew (brew) not found')
            return None
        except Exception as e:
            logger.error(f'Failed to upgrade {formula}: {e}')
            return None

        version = self.__class__._get_formula_version(formula)
        return Package(name=pkg.name, version=version)

    @override
    def packages(self) -> list[Package]:
        """Lists all installed formulas in Homebrew.

        Returns:
            A list of installed packages
        """
        logger = logging.getLogger('porringer.brew.packages')
        packages: list[Package] = []

        try:
            # List installed formulas in JSON format
            result = subprocess.run(
                ['brew', 'list', '--formula', '--json'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                logger.warning('Failed to list installed formulas')
                return packages

            # The JSON output is a list of formula objects
            # Each has 'name' and 'installed' (list with version info)
            formulas = json.loads(result.stdout) if result.stdout.strip() else []
            for formula in formulas:
                name = formula.get('name', 'unknown')
                # Get the first installed version
                installed = formula.get('installed', [])
                version = installed[0].get('version', 'unknown') if installed else 'unknown'
                packages.append(
                    Package(
                        name=name,
                        version=version,
                    )
                )

        except FileNotFoundError:
            logger.error('Homebrew (brew) not found')
        except json.JSONDecodeError:
            logger.warning('Could not parse installed formulas list')
        except Exception as e:
            logger.error(f'Failed to list formulas: {e}')

        return packages

    @staticmethod
    def _get_formula_version(formula: str) -> str | None:
        """Gets the installed version for a formula.

        Args:
            formula: The formula name

        Returns:
            The version string, or None if not found
        """
        try:
            result = subprocess.run(
                ['brew', 'info', formula, '--json=v2'],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                info = json.loads(result.stdout)
                formulas = info.get('formulae', [])
                if formulas:
                    installed = formulas[0].get('installed', [])
                    if installed:
                        return installed[0].get('version')
        except Exception:
            pass

        return None
