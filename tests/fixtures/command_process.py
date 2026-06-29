"""Porringer subprocess simulation helpers built on pytest-subprocess."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import pytest
from pytest_subprocess import FakeProcess
from pytest_subprocess.process_recorder import ProcessRecorder
from pytest_subprocess.types import COMMAND


@dataclass(frozen=True, slots=True)
class CommandCall:
    """A normalized subprocess invocation observed by pytest-subprocess."""

    argv: tuple[str, ...]
    cwd: str | None
    env: dict[str, str] | None
    returncode: int | None


def _command_argument(value: object) -> object:
    """Return a pytest-subprocess-compatible command argument."""
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return value


def _argv(value: object) -> tuple[str, ...]:
    """Convert a subprocess command into a tuple of displayable argv parts."""
    if isinstance(value, list | tuple):
        return tuple(str(_command_argument(part)) for part in value)
    return (str(_command_argument(value)),)


def _env(value: object) -> dict[str, str] | None:
    """Convert a subprocess env mapping into a plain string dictionary."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return {str(key): str(env_value) for key, env_value in value.items()}


class CommandProcess:
    """Project-shaped wrapper around pytest-subprocess's ``fp`` fixture."""

    def __init__(self, fake_process: FakeProcess) -> None:
        """Store the backing pytest-subprocess fake process."""
        self._fake_process = fake_process
        self._recorders: list[ProcessRecorder] = []

    def any(self, *, min: int | None = None, max: int | None = None) -> object:
        """Return a pytest-subprocess wildcard argument."""
        return self._fake_process.any(min=min, max=max)

    def regex(self, pattern: str, flags: int = 0) -> object:
        """Return a pytest-subprocess regex argument."""
        return self._fake_process.regex(pattern, flags)

    def script(
        self,
        command: Sequence[object],
        *,
        stdout: str | bytes | Sequence[str | bytes] | None = None,
        stderr: str | bytes | Sequence[str | bytes] | None = None,
        returncode: int = 0,
        occurrences: int = 1,
    ) -> ProcessRecorder:
        """Register a canned response for an exact command pattern."""
        pattern = cast(COMMAND, tuple(_command_argument(part) for part in command))
        recorder = self._fake_process.register(
            pattern,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            occurrences=occurrences,
        )
        self._recorders.append(recorder)
        return recorder

    def script_prefix(
        self,
        command_prefix: Sequence[object],
        *,
        stdout: str | bytes | Sequence[str | bytes] | None = None,
        stderr: str | bytes | Sequence[str | bytes] | None = None,
        returncode: int = 0,
        occurrences: int = 1,
    ) -> ProcessRecorder:
        """Register a canned response for a command prefix with any trailing args."""
        return self.script(
            [*command_prefix, self.any(min=0)],
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            occurrences=occurrences,
        )

    @property
    def calls(self) -> list[CommandCall]:
        """Observed registered calls in process creation order."""
        processes = [process for recorder in self._recorders for process in recorder.calls]
        return [
            CommandCall(
                argv=_argv(process.args),
                cwd=str(process.kwargs.get('cwd'))
                if process.kwargs and process.kwargs.get('cwd') is not None
                else None,
                env=_env(process.kwargs.get('env')) if process.kwargs else None,
                returncode=process.returncode,
            )
            for process in sorted(processes, key=lambda process: process.pid)
        ]

    @property
    def argv_list(self) -> list[list[str]]:
        """Observed argv lists in process creation order."""
        return [list(call.argv) for call in self.calls]

    def assert_called_with(
        self,
        command: Sequence[object],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """Assert that a matching command was observed."""
        expected_argv = tuple(str(_command_argument(part)) for part in command)
        expected_env = dict(env) if env is not None else None
        for call in self.calls:
            if call.argv != expected_argv:
                continue
            if cwd is not None and call.cwd != cwd:
                continue
            if expected_env is not None and call.env != expected_env:
                continue
            return
        raise AssertionError(f'No call matching argv={list(expected_argv)!r} cwd={cwd!r}; observed: {self.calls!r}')

    def assert_no_calls(self) -> None:
        """Assert that no registered subprocess calls were observed."""
        if self.calls:
            raise AssertionError(f'Expected no subprocess calls; observed: {self.calls!r}')


@pytest.fixture
def command_process(fp: FakeProcess) -> CommandProcess:
    """Return the project subprocess simulation helper."""
    return CommandProcess(fp)
