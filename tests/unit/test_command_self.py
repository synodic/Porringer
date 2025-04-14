"""Test the command 'self'"""

from logging import Logger

import pytest

from porringer.api import API
from porringer.schema import (
    APIParameters,
    CheckPorringerParameters,
    LocalConfiguration,
    UpdatePorringerParameters,
)


class TestCommandSelf:
    """Test the command 'self'"""

    @staticmethod
    def test_self_update_check() -> None:
        """Test the self update check"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = CheckPorringerParameters()

        with pytest.raises(NotImplementedError):
            api.porringer.check()

    @staticmethod
    def test_self_update() -> None:
        """Test the self update"""
        config = LocalConfiguration()
        parameters = APIParameters(logger=Logger('test'))
        api = API(config, parameters)

        params = UpdatePorringerParameters()

        with pytest.raises(NotImplementedError):
            api.porringer.update()
