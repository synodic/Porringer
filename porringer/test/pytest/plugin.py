"""Plugin"""

from typing import cast

import pytest
from porringer.core.schema import Distribution, PluginParameters
from porringer.test.pytest.variants import porringer_distribution_list


@pytest.fixture(name='plugin_distributions', scope='session', params=porringer_distribution_list)
def fixture_plugin_distributions(request: pytest.FixtureRequest) -> Distribution:
    """Fixture for plugin distributions.

    Args:
        request: The pytest request object for parameterization.

    Returns:
        Distribution: The distribution object for the plugin.
    """
    return cast(Distribution, request.param)


@pytest.fixture(
    name='plugin_parameters',
    scope='session',
)
def fixture_plugin_parameters(plugin_distributions: Distribution) -> PluginParameters:
    """Fixture for plugin parameters.

    Args:
        plugin_distributions: The distribution object for the plugin.

    Returns:
        PluginParameters: The parameters object for the plugin.
    """
    return PluginParameters(distribution=plugin_distributions)
