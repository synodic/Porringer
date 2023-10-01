"""Test the command 'self' """
from logging import Logger

import pytest

from porringer.api import API
from porringer.schema import (
    CheckPorringerParameters,
    LocalConfiguration,
    Parameters,
    UpdatePorringerParameters,
)


class TestCommandSelf:
    """_summary_"""

    def test_self_update_check(self) -> None:
        """_summary_"""

        config = LocalConfiguration()
        parameters = Parameters(logger=Logger("test"))
        api = API(config, parameters)

        with pytest.raises(NotImplementedError):
            params = CheckPorringerParameters()
            api.check_porringer(params)

    def test_self_update(self) -> None:
        """_summary_"""

        config = LocalConfiguration()
        parameters = Parameters(logger=Logger("test"))
        api = API(config, parameters)

        with pytest.raises(NotImplementedError):
            params = UpdatePorringerParameters()
            api.update_porringer(params)
