"""Plugin"""

from typing import cast

import pytest
from porringer_core.schema import Distribution, PluginParameters

from pytest_porringer.variants import porringer_distribution_list


@pytest.fixture(name="plugin_distributions", scope="session", params=porringer_distribution_list)
def fixture_plugin_distributions(request: pytest.FixtureRequest) -> Distribution:
    """_summary_

    Args:
        request: _description_

    Returns:
        _description_
    """

    return cast(Distribution, request.param)


@pytest.fixture(
    name="plugin_parameters",
    scope="session",
)
def fixture_plugin_parameters(plugin_distributions: Distribution) -> PluginParameters:
    """_summary_

    Args:
        plugin_distributions: _description_

    Returns:
        _description_
    """

    return PluginParameters(distribution=plugin_distributions)
