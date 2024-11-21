"""Test the command 'self'"""

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
    """Test the command 'self'"""

    @staticmethod
    def test_self_update_check() -> None:
        """Test the self update check"""
        config = LocalConfiguration()
        parameters = Parameters(logger=Logger('test'))
        api = API(config, parameters)

        params = CheckPorringerParameters()

        with pytest.raises(NotImplementedError):
            api.check_porringer(params)

    @staticmethod
    def test_self_update() -> None:
        """Test the self update"""
        config = LocalConfiguration()
        parameters = Parameters(logger=Logger('test'))
        api = API(config, parameters)

        params = UpdatePorringerParameters()

        with pytest.raises(NotImplementedError):
            api.update_porringer(params)
