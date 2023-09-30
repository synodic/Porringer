"""Version utilities"""

import os
import sys


def is_pipx_installation() -> bool:
    """_summary_

    Returns:
        _description_
    """
    return sys.prefix.split(os.sep)[-3:-1] == ["pipx", "venvs"]
