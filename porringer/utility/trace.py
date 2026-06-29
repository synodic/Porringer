"""Opt-in command trace artifacts for debugging wrapper behaviour."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
import uuid
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from porringer.schema.observability import SCHEMA_VERSION, ActionRef
from porringer.utility.tool_environment import TRACE_ENVIRONMENT_KEYS, environment_subset

logger = logging.getLogger(__name__)

TRACE_DIR_ENV = 'PORRINGER_TRACE_DIR'
_TAIL_CHARS = 4000
_TRACE_SUMMARY_LIMIT = 8
_TRACE_ARGV_MAX = 220
_TRACE_ARGV_PREFIX = _TRACE_ARGV_MAX - 3
_TRACE_JSON_SEPARATORS = (',', ':')
_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar('porringer_trace_context', default=None)
_TRACE_DIRECTORY_CACHE: dict[str, Path] = {}
_TRACE_IO_WARNED = False


def _warn_trace_io_failure(message: str, error: OSError) -> None:
    """Log a single warning when tracing is enabled but I/O fails.

    Tracing is an opt-in debugging aid, so a misconfigured
    ``PORRINGER_TRACE_DIR`` (unwritable path, permission denied) must
    never break a sync.  Without a signal, though, the user silently
    gets zero artifacts.  Emit one warning per process so the failure
    is visible without flooding the log on every subprocess.
    """
    module: Any = sys.modules[__name__]
    if getattr(module, '_TRACE_IO_WARNED', False):
        return
    module._TRACE_IO_WARNED = True
    logger.warning('%s: %s', message, error)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Action-level context attached to command trace artifacts."""

    mode: str
    action_ref: ActionRef | None = None
    action_description: str | None = None
    action_kind: str | None = None
    installer: str | None = None
    package_name: str | None = None
    operation: str | None = None

    def payload(self) -> dict[str, Any]:
        """Return JSON-serializable context fields for a trace payload."""
        action_payload = {
            'description': self.action_description,
            'kind': self.action_kind,
            'installer': self.installer,
            'package_name': self.package_name,
        }
        action_payload = {key: value for key, value in action_payload.items() if value is not None}
        return {
            'mode': self.mode,
            'action_ref': self.action_ref.model_dump(mode='json') if self.action_ref is not None else None,
            'action_id': self.action_ref.action_id if self.action_ref is not None else None,
            'action': action_payload or None,
            'operation': self.operation,
        }


def current_trace_context() -> TraceContext | None:
    """Return the active trace context for this task."""
    return _CURRENT_TRACE_CONTEXT.get()


@contextmanager
def use_trace_context(context: TraceContext | None) -> Generator[None]:
    """Temporarily set trace context for commands started in this task."""
    token = _CURRENT_TRACE_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TRACE_CONTEXT.reset(token)


def trace_directory() -> Path | None:
    """Return the active trace directory, creating it when enabled."""
    raw = os.environ.get(TRACE_DIR_ENV)
    if not raw:
        return None
    if cached := _TRACE_DIRECTORY_CACHE.get(raw):
        return cached

    directory = Path(raw)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _warn_trace_io_failure(f'Could not create trace directory {directory}', error)
        return None
    _TRACE_DIRECTORY_CACHE[raw] = directory
    return directory


def _coerce_text(value: str | bytes | None) -> str:
    """Convert subprocess output to text for trace storage."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _tail(value: str | bytes | None) -> str:
    """Return a bounded tail of subprocess output."""
    text = _coerce_text(value)
    return text[-_TAIL_CHARS:]


def _which(command: str | None) -> str | None:
    """Resolve *command* on PATH without raising."""
    if not command:
        return None
    try:
        return shutil.which(command)
    except OSError:
        return None


@dataclass(slots=True)
class CommandTrace:
    """Lifecycle object for one subprocess trace."""

    argv: tuple[str, ...]
    cwd: str | None
    started_ns: int
    which_before: str | None
    context: TraceContext | None

    @classmethod
    def start(cls, args: Sequence[object], *, cwd: str | os.PathLike[str] | None = None) -> CommandTrace:
        """Capture pre-run diagnostics for *args*."""
        argv = tuple(str(arg) for arg in args)
        command = argv[0] if argv else None
        return cls(
            argv=argv,
            cwd=str(cwd) if cwd is not None else None,
            started_ns=time.time_ns(),
            which_before=_which(command),
            context=current_trace_context(),
        )

    def finish(
        self,
        *,
        returncode: int | None,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
        error: str | None = None,
    ) -> None:
        """Write the trace artifact when tracing is enabled."""
        directory = trace_directory()
        if directory is None:
            return

        finished_ns = time.time_ns()
        command = self.argv[0] if self.argv else None
        payload: dict[str, Any] = {
            'trace_schema_version': SCHEMA_VERSION,
            'argv': list(self.argv),
            'cwd': self.cwd,
            'returncode': returncode,
            'duration_ms': round((finished_ns - self.started_ns) / 1_000_000, 3),
            'stdout_tail': _tail(stdout),
            'stderr_tail': _tail(stderr),
            'error': error,
            'env_subset': environment_subset(TRACE_ENVIRONMENT_KEYS),
            'which_before': self.which_before,
            'which_after': _which(command),
        }
        if self.context is not None:
            payload.update(self.context.payload())
        else:
            payload.update({'mode': None, 'action_ref': None, 'action_id': None, 'action': None, 'operation': None})
        filename = f'{self.started_ns}-{uuid.uuid4().hex[:8]}.json'
        try:
            (directory / filename).write_text(
                json.dumps(payload, separators=_TRACE_JSON_SEPARATORS),
                encoding='utf-8',
            )
        except OSError as io_error:
            _warn_trace_io_failure(f'Could not write trace artifact to {directory}', io_error)
            return


def format_trace_summary(trace_dir: Path, *, limit: int = _TRACE_SUMMARY_LIMIT) -> str:
    """Return a compact, human-readable summary of trace artifacts."""
    files = sorted(trace_dir.glob('*.json'))
    lines = [f'trace directory: {trace_dir}']
    if not files:
        lines.append('no command trace files were written')
        return '\n'.join(lines)

    lines.append(f'command traces: {len(files)}')
    for path in files[-limit:]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except OSError, ValueError:
            lines.append(f'- {path.name}: unreadable')
            continue
        argv = ' '.join(str(arg) for arg in data.get('argv', []))
        if len(argv) > _TRACE_ARGV_MAX:
            argv = f'{argv[:_TRACE_ARGV_PREFIX]}...'
        lines.append(f'- {path.name}: rc={data.get("returncode")} {argv}')
        if data.get('error'):
            lines.append(f'  error: {data["error"]}')
        stderr_tail = str(data.get('stderr_tail') or '').strip().splitlines()
        if stderr_tail:
            lines.append(f'  stderr: {stderr_tail[-1]}')
    return '\n'.join(lines)
