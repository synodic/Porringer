"""Version utilities"""

import os
import sys
from logging import Logger

from porringer.utility.subprocess import call


def is_pipx_installation() -> bool:
    """_summary_

    Returns:
        _description_
    """
    return sys.prefix.split(os.sep)[-3:-1] == ['pipx', 'venvs']


def update_porringer(logger: Logger) -> None:
    """_summary_

    Args:
        logger: _description_

    Raises:
        NotImplementedError: _description_
    """
    if is_pipx_installation():
        call(['pipx', 'upgrade', 'porringer'], logger)
    else:
        raise NotImplementedError()


def check_porringer(logger: Logger) -> None:
    """_summary_

    Args:
        logger: _description_

    Raises:
        NotImplementedError: _description_
    """
    if is_pipx_installation():
        call(['pipx', 'upgrade', 'porringer'], logger)
    else:
        raise NotImplementedError()
