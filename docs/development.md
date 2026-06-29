---
icon: lucide/wrench
---

# Development

Use this page when working on Porringer itself. The normal loop is install dependencies, run the hermetic test suite, and build the documentation site.

```powershell
pdm install
pdm run test
pdm run generate
```

Development scripts are defined in `pyproject.toml` under `[tool.pdm.scripts]`.

## Testing Goals

Porringer tests should stay focused on behavior the project owns:

1. Assert the argv Porringer builds and the data it parses, not the exact wording of external tools.
2. Prefer self-maintaining evidence from a real tool, a pure inverse, or an invariant.
3. Define plugin-agnostic behavior once in the base classes under `porringer/test/pytest/` instead of copying it into every plugin test.

Two operational rules follow from those goals:

- `pdm run test` is hermetic. Outbound sockets are disabled and loopback is allowed.
- Tests that exercise real wrapped tools skip automatically when those tools are absent.

When a corner case keeps needing tests, prefer constraining the type or contract that allowed the corner case.

## Test Commands

Run the full suite:

```powershell
pdm run test
```

Run linting and type checks:

```powershell
pdm run lint
```

Build the docs and generated schema:

```powershell
pdm run generate
```

`pdm run generate` writes `docs/schema.json` and runs `zensical build --clean --strict`.

## Command-Contract Tests

Command-contract tests use `pytest-subprocess` to simulate wrapped tools without invoking the host machine's real `pip`, `pipx`, `npm`, or similar executables. These tests should assert the command flow Porringer owns.

Use `dirty-equals` matchers for volatile path, environment, and timing fields. This is the preferred place to cover clean-environment expectations, such as installing `pipx` with `pip` before using `pipx` in a later phase.

## Smoke Tests

Smoke tests use local fixture packages instead of public registries where possible. The Python fixture builds a local wheel for `porringer-smoke-python` and exposes a stable command:

```shell
porringer-smoke-python --version
```

Real wrapped-tool checks run in disposable user and tool homes, then skip when the underlying tool is not installed.

## Command Traces

Set `PORRINGER_TRACE_DIR` to collect compact JSON traces during manual runs or tests:

```powershell
$env:PORRINGER_TRACE_DIR = "D:\temp\porringer-trace"
pdm run porr env info --json
```

Each traced subprocess records:

- `trace_schema_version`
- argv and cwd
- return code and duration
- stdout and stderr tails
- selected environment variables
- before and after executable resolution

When a command runs while inspecting or executing a manifest action, the trace also includes `mode`, `action_ref`, `action_id`, compact action metadata, and an operation label. The `action_id` matches the IDs in `porringer preview --json` and progress event snapshots.

Observed package, project, and SCM commands emit stdout and stderr lines as progress events for live logs. Their final command result keeps only an output tail by default so chatty tools do not keep unbounded output in memory. Use `run_command(..., progress=..., output_tail_lines=None)` only when a call site truly needs full retained output.

## Environment Diagnostics

Use `porr env info` when a wrapper behaves differently through Porringer than it does in a terminal:

```powershell
pdm run porr env info
pdm run porr env info --json
```

The command reports Python and system details, Porringer directories, PATH synchronization, tool-home environment variables, optional plugin availability, resolved tools, versions, and runtime context.

## Performance Probe

For local latency checks, run the dependency-free probe script:

```powershell
pdm run python scripts/perf_probe.py --rounds 2 --runtime-rounds 1 --skip-inspect
```

The probe reports cold and warm plugin discovery, plugin copying, pre-discovered manifest loading, fast plugin discovery, deferred runtime resolution, full discovery with runtime resolution, and fast or complete inspect timing.

Use optional flags for narrower questions:

```powershell
pdm run python scripts/perf_probe.py --include-cli
pdm run python scripts/perf_probe.py --include-trace
```

`--include-cli` times `porringer preview --json` when the console script is on PATH. `--include-trace` measures disabled and enabled command-trace finalization.
