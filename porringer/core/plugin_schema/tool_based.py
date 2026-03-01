"""Shared base for plugins backed by a command-line tool.

Provides the ``tool_name()`` / ``is_available()`` / ``tool_version()``
triple so that ``Environment``, ``ProjectEnvironment``, and
``ScmEnvironment`` share a single implementation instead of duplicating
the ``shutil.which`` logic.

The three async helper class-methods — ``_run_json_command``,
``_run_text_command``, and ``_run_bool_command`` — use native
``asyncio.create_subprocess_exec`` so that plugin I/O never blocks
the event loop.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from porringer.core.schema import Plugin


class ToolBasedPlugin(Plugin):
    """Intermediate base for plugins backed by a CLI tool.

    Subclasses that override `tool_name()` to return a non-`None`
    string get automatic availability detection via `shutil.which`.
    Subclasses with `tool_name() → None` (the default) are always
    considered available.
    """

    @classmethod
    def tool_name(cls) -> str | None:
        """Return the CLI executable name this plugin wraps.

        Override to declare which command-line tool the plugin uses.
        The base `is_available()` implementation uses this value
        with `shutil.which` to test whether the tool is on PATH.

        Returns `None` for plugins that are not backed by a single CLI
        tool (the default).  Those plugins are always considered available.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if the underlying tool is available on the system.

        When `tool_name()` returns a string, `shutil.which` is
        used to verify the executable exists on PATH.  When
        `tool_name()` returns `None`, the plugin is always
        considered available.
        """
        name = cls.tool_name()
        if name is None:
            return True
        return shutil.which(name) is not None

    @classmethod
    def tool_version(cls) -> Version | None:
        """Returns the PEP 440 version of the underlying CLI tool.

        The default implementation runs `<tool_name> --version`, extracts the
        first version-like pattern from the combined stdout/stderr output, and
        parses it as a `Version`.

        Returns `None` when `tool_name()` is `None`, the subprocess
        fails, or the output cannot be parsed as a valid PEP 440 version.

        Subclasses may override this method if their tool's version output
        requires special parsing.

        Returns:
            The parsed tool version, or `None`.
        """
        name = cls.tool_name()
        if name is None:
            return None

        try:
            result = subprocess.run(
                [name, '--version'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            output = result.stdout + result.stderr
        except OSError, subprocess.SubprocessError:
            return None

        match = re.search(r'v?\d+\.\d+(?:\.\d+)*', output)
        if match is None:
            return None

        try:
            return Version(match.group(0))
        except InvalidVersion:
            return None

    @classmethod
    async def _run_json_command(cls, args: list[str], *, check: bool = False) -> Any | None:
        """Run a CLI command and parse its stdout as JSON.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Centralises the common pattern of running a subprocess, reading
        its standard output, and parsing it as JSON while handling the
        three failure modes every plugin must deal with:

        * ``FileNotFoundError`` — the tool is not on PATH.
        * ``OSError`` / ``TimeoutError`` — the tool failed or timed out.
        * ``json.JSONDecodeError`` — the output was not valid JSON.

        When *check* is ``True``, a non-zero exit code is treated as an
        error and ``None`` is returned.

        Args:
            args: Command and arguments (e.g. ``['npm', 'ls', '-g', '--json']``).
            check: Whether to treat a non-zero exit code as an error.

        Returns:
            The parsed JSON value (dict, list, etc.), or ``None`` when
            the command cannot be executed or its output is not valid
            JSON.
        """
        logger = logging.getLogger(f'porringer.{cls.tool_name()}.json_command')
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            if proc.returncode != 0:
                if check:
                    stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
                    logger.error('%s exited with code %d: %s', args[0], proc.returncode, stderr)
                else:
                    logger.warning('%s exited with code %d', args[0], proc.returncode)
                return None
            return json.loads(stdout) if stdout.strip() else None
        except FileNotFoundError:
            logger.error('%s not found on PATH', args[0])
        except (OSError, TimeoutError) as e:
            logger.error('Failed to run %s: %s', args[0], e)
        except json.JSONDecodeError as e:
            logger.warning('Could not parse JSON output from %s: %s', args[0], e)
        return None

    @classmethod
    async def _run_text_command(cls, args: list[str], *, check: bool = False) -> str | None:
        """Run a CLI command and return its stdout as text.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Centralises the common pattern of running a subprocess and
        returning its standard output while handling failure modes:

        * ``FileNotFoundError`` — the tool is not on PATH.
        * ``OSError`` / ``TimeoutError`` — the tool failed or timed out.

        When *check* is ``True``, a non-zero exit code is treated as
        an error and ``None`` is returned.

        Args:
            args: Command and arguments.
            check: Whether to treat a non-zero exit code as an error.

        Returns:
            The stdout string, or ``None`` on failure.
        """
        logger = logging.getLogger(f'porringer.{cls.tool_name()}.text_command')
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            if proc.returncode != 0:
                if check:
                    logger.error('%s exited with code %d', args[0], proc.returncode)
                else:
                    logger.warning('%s exited with code %d', args[0], proc.returncode)
                return None
            return stdout
        except FileNotFoundError:
            logger.error('%s not found on PATH', args[0])
        except (OSError, TimeoutError) as e:
            logger.error('Failed to run %s: %s', args[0], e)
        return None

    @classmethod
    async def _run_bool_command(
        cls,
        args: list[str],
        *,
        cwd: Path | None = None,
        label: str = 'command',
    ) -> bool:
        """Run a CLI command and return whether it succeeded.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Logs stdout at info level and stderr at error level on failure.
        Returns ``True`` when the process exits with code 0.

        Args:
            args: Command and arguments.
            cwd: Working directory for the subprocess.
            label: Context label used in the logger name and error messages.

        Returns:
            ``True`` if the process exited cleanly, ``False`` otherwise.
        """
        logger = logging.getLogger(f'porringer.{cls.tool_name()}.{label}')
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            logger.info(stdout)
            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
                logger.error(stderr)
                return False
        except FileNotFoundError:
            logger.error('%s not found on PATH', args[0])
            return False
        except (OSError, TimeoutError) as e:
            logger.error('Failed to run %s: %s', label, e)
            return False
        return True
