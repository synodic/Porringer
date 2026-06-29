"""Helpers for test smoke packages.

Tests for local smoke package artifacts.
"""

import subprocess

from tests.fixtures.smoke_packages import PythonSmokePackage, create_venv_with_pip, venv_script


def test_python_smoke_wheel_installs_and_console_script_runs(
    python_smoke_package: PythonSmokePackage,
    tmp_path,
) -> None:
    """The local wheel installs into a clean venv and exposes its console script."""
    python = create_venv_with_pip(tmp_path / 'venv')

    subprocess.run(
        [str(python), '-m', 'pip', 'install', '--no-index', str(python_smoke_package.wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    script = venv_script(tmp_path / 'venv', python_smoke_package.executable_name)
    result = subprocess.run([str(script), '--version'], check=True, capture_output=True, text=True)

    assert result.stdout.strip() == python_smoke_package.version_output
