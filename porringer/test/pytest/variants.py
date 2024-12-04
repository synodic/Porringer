"""Provides test data for plugin tests"""

from collections.abc import Sequence

from packaging.version import Version

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Distribution
from porringer.test.mock.environment import MockEnvironment


def _mock_environment_list() -> Sequence[type[Environment]]:
    """Mocked list of environments

    Returns:
        List of mock environments
    """
    variants = []

    # Default
    variants.append(MockEnvironment)

    return variants


def _porringer_distribution_list() -> Sequence[Distribution]:
    """Mocked list of plugin distributions

    Returns:
        Distributions for the plugin
    """
    variants = []

    # Default
    version = Version('0.0.0')
    variants.append(Distribution(version=version))

    return variants


environment_variants = _mock_environment_list()
porringer_distribution_list = _porringer_distribution_list()
