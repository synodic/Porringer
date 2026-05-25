"""Local smoke package artifacts for real-tool lifecycle tests."""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

_PACKAGE_NAME = 'porringer-smoke-python'
_MODULE_NAME = 'porringer_smoke_python'
_VERSION = '0.1.0'


@dataclass(frozen=True, slots=True)
class PythonSmokePackage:
    """A local wheel with a stable console script."""

    name: str
    version: str
    wheel: Path
    executable_name: str
    version_output: str

    @property
    def requirement(self) -> str:
        """Return the package requirement used by installer smoke tests."""
        return f'{self.name}=={self.version}'


def _hash_record(content: bytes) -> str:
    """Return a wheel RECORD hash for *content*."""
    digest = hashlib.sha256(content).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return f'sha256={encoded}'


def _write_python_smoke_wheel(wheelhouse: Path) -> Path:
    """Write the local Python smoke wheel and return its path."""
    dist_info = f'{_MODULE_NAME}-{_VERSION}.dist-info'
    wheel_name = f'{_MODULE_NAME}-{_VERSION}-py3-none-any.whl'
    wheel = wheelhouse / wheel_name
    files = {
        f'{_MODULE_NAME}/__init__.py': f"__version__ = '{_VERSION}'\n".encode(),
        f'{_MODULE_NAME}/__main__.py': b'from .cli import main\nraise SystemExit(main())\n',
        f'{_MODULE_NAME}/cli.py': (
            'import argparse\n\n'
            'def main() -> int:\n'
            '    parser = argparse.ArgumentParser(prog="porringer-smoke-python")\n'
            '    parser.add_argument("--version", action="store_true")\n'
            '    args = parser.parse_args()\n'
            '    if args.version:\n'
            f'        print("{_PACKAGE_NAME} {_VERSION}")\n'
            '    else:\n'
            '        print("porringer smoke ready")\n'
            '    return 0\n'
        ).encode(),
        f'{dist_info}/METADATA': (
            'Metadata-Version: 2.1\n'
            f'Name: {_PACKAGE_NAME}\n'
            f'Version: {_VERSION}\n'
            'Summary: Local Porringer smoke test package\n'
        ).encode(),
        f'{dist_info}/WHEEL': (
            b'Wheel-Version: 1.0\nGenerator: porringer-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
        ),
        f'{dist_info}/entry_points.txt': (f'[console_scripts]\n{_PACKAGE_NAME} = {_MODULE_NAME}.cli:main\n').encode(),
    }
    record_rows = [f'{path},{_hash_record(content)},{len(content)}' for path, content in files.items()]
    record_rows.append(f'{dist_info}/RECORD,,')
    files[f'{dist_info}/RECORD'] = ('\n'.join(record_rows) + '\n').encode()

    with zipfile.ZipFile(wheel, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return wheel


@pytest.fixture(scope='session')
def python_smoke_package(tmp_path_factory: pytest.TempPathFactory) -> PythonSmokePackage:
    """Build a local wheel used by real-tool smoke tests."""
    wheelhouse = tmp_path_factory.mktemp('python_smoke_wheelhouse')
    wheel = _write_python_smoke_wheel(wheelhouse)
    return PythonSmokePackage(
        name=_PACKAGE_NAME,
        version=_VERSION,
        wheel=wheel,
        executable_name=_PACKAGE_NAME,
        version_output=f'{_PACKAGE_NAME} {_VERSION}',
    )


def venv_python(venv: Path) -> Path:
    """Return the Python executable inside *venv*."""
    if sys.platform == 'win32':
        return venv / 'Scripts' / 'python.exe'
    return venv / 'bin' / 'python'


def venv_script(venv: Path, name: str) -> Path:
    """Return a console-script path inside *venv*."""
    if sys.platform == 'win32':
        return venv / 'Scripts' / f'{name}.exe'
    return venv / 'bin' / name


def create_venv_with_pip(venv: Path) -> Path:
    """Create a venv and return its Python executable."""
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv_python(venv)
    subprocess.run([str(python), '-m', 'pip', '--version'], check=True, capture_output=True, text=True)
    return python
