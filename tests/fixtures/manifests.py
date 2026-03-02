"""Session-scoped manifest directory fixtures.

Each fixture creates a temporary directory once per session, writes a
``porringer.json`` file, and yields the directory ``Path``.  Tests that
previously created their own ``tempfile.TemporaryDirectory`` with the
same JSON can use these instead.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture(scope='session')
def manifest_simple(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with a single Python package (``requests``).

    JSON::

        {'version': '1', 'packages': {'python': ['requests']}}
    """
    d = tmp_path_factory.mktemp('manifest_simple')
    (d / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests']}}))
    return d


@pytest.fixture(scope='session')
def manifest_simple_with_post_sync(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with a single package and a ``post_sync`` command.

    JSON::

        {'version': '1', 'packages': {'python': ['requests']}, 'post_sync': ['echo hello']}
    """
    d = tmp_path_factory.mktemp('manifest_simple_post_sync')
    (d / 'porringer.json').write_text(
        json.dumps({'version': '1', 'packages': {'python': ['requests']}, 'post_sync': ['echo hello']})
    )
    return d


@pytest.fixture(scope='session')
def manifest_multi_package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with two Python packages (``requests``, ``flask``).

    JSON::

        {'version': '1', 'packages': {'python': ['requests', 'flask']}}
    """
    d = tmp_path_factory.mktemp('manifest_multi_package')
    (d / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['requests', 'flask']}}))
    return d


@pytest.fixture(scope='session')
def manifest_multi_package_with_post_sync(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with two packages and a ``post_sync`` command.

    JSON::

        {'version': '1', 'packages': {'python': ['requests', 'flask']}, 'post_sync': ['echo done']}
    """
    d = tmp_path_factory.mktemp('manifest_multi_post_sync')
    (d / 'porringer.json').write_text(
        json.dumps({
            'version': '1',
            'packages': {'python': ['requests', 'flask']},
            'post_sync': ['echo done'],
        })
    )
    return d


@pytest.fixture(scope='session')
def manifest_multi_package_two_post_sync(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with two packages and two ``post_sync`` commands.

    JSON::

        {'version': '1', 'packages': {'python': ['requests']}, 'post_sync': ['echo hello', 'echo world']}
    """
    d = tmp_path_factory.mktemp('manifest_two_post_sync')
    (d / 'porringer.json').write_text(
        json.dumps({
            'version': '1',
            'packages': {'python': ['requests']},
            'post_sync': ['echo hello', 'echo world'],
        })
    )
    return d


@pytest.fixture(scope='session')
def manifest_three_packages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with three Python packages.

    JSON::

        {'version': '1', 'packages': {'python': ['requests', 'flask', 'pytest']}}
    """
    d = tmp_path_factory.mktemp('manifest_three_packages')
    (d / 'porringer.json').write_text(
        json.dumps({'version': '1', 'packages': {'python': ['requests', 'flask', 'pytest']}})
    )
    return d


@pytest.fixture(scope='session')
def manifest_packaging(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with ``packaging`` (always installed in dev).

    JSON::

        {'version': '1', 'packages': {'python': ['packaging']}}
    """
    d = tmp_path_factory.mktemp('manifest_packaging')
    (d / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['packaging']}}))
    return d


@pytest.fixture(scope='session')
def manifest_nonexistent_package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with a package name that should never be installed.

    JSON::

        {'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}}
    """
    d = tmp_path_factory.mktemp('manifest_nonexistent')
    (d / 'porringer.json').write_text(
        json.dumps({'version': '1', 'packages': {'python': ['zzz-nonexistent-package-xyz']}})
    )
    return d


@pytest.fixture(scope='session')
def manifest_packaging_version_satisfied(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with ``packaging>=1.0`` (always satisfied).

    JSON::

        {'version': '1', 'packages': {'python': ['packaging>=1.0']}}
    """
    d = tmp_path_factory.mktemp('manifest_pkg_satisfied')
    (d / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['packaging>=1.0']}}))
    return d


@pytest.fixture(scope='session')
def manifest_packaging_version_unsatisfied(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Manifest with ``packaging>=99999`` (never satisfied).

    JSON::

        {'version': '1', 'packages': {'python': ['packaging>=99999']}}
    """
    d = tmp_path_factory.mktemp('manifest_pkg_unsatisfied')
    (d / 'porringer.json').write_text(json.dumps({'version': '1', 'packages': {'python': ['packaging>=99999']}}))
    return d
