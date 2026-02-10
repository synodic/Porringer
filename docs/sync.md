# Sync Command

The sync command reconciles your development environment with the desired state declared in a manifest file.

## Manifest Files

Porringer looks for manifests in this order:

1. `porringer.json` - JSON manifest file
2. `pyproject.toml` - `[tool.porringer]` section

## Manifest Schema

| Field         | Type   | Description                                                                   |
| ------------- | ------ | ----------------------------------------------------------------------------- |
| `version`     | string | Schema version (currently `"1"`)                                              |
| `packages`    | object | Packages to install per ecosystem (e.g. `{"python": ["requests"]}`)           |
| `tools`       | object | CLI tools to install per ecosystem (e.g. `{"python": ["pdm"]}`)               |
| `projects`    | object | Project sync targets per ecosystem (e.g. `{"python": []}`)                    |
| `runtimes`    | object | Language runtimes per ecosystem (e.g. `{"python": ["3.12"]}`)                 |
| `preferences` | object | Optional installer overrides per ecosystem (e.g. `{"python": "uv"}`)          |
| `post_sync`   | array  | Commands to run after synchronisation                                         |
| `name`        | string | Optional human-readable project name                                          |
| `description` | string | Optional short description                                                    |
| `author`      | string | Optional author or organisation name                                          |
| `url`         | string | Optional project URL                                                          |

### Kind Sections

The manifest groups entries by **kind** (`packages`, `tools`, `projects`, `runtimes`), each containing a dict keyed by **ecosystem** (e.g. `"python"`, `"node"`, `"system"`). Ecosystem names are free-form strings declared by plugins — adding a new ecosystem requires zero core changes.

- **`packages`** — install individual packages (e.g. `ruff`, `typescript`).
- **`tools`** — install CLI tools in isolated environments (e.g. `pdm` via `pipx`).
- **`projects`** — synchronise an entire project's dependencies from its lock file.
- **`runtimes`** — install language runtimes (e.g. Python 3.12 via `pim`/`pyenv`).

Well-known ecosystems and their default installers:

| Ecosystem | Kind      | Description                        | Default installers            |
| --------- | --------- | ---------------------------------- | ----------------------------- |
| `python`  | packages  | Python packages                    | `uv`, `pip`                   |
| `python`  | tools     | Python CLI tools                   | `pipx`                        |
| `python`  | runtimes  | Python runtimes                    | `pim`, `pyenv`                |
| `python`  | projects  | Python project sync                | `uv`, `pdm`, `poetry`         |
| `system`  | packages  | OS-level packages                  | `brew`, `apt`, `winget`       |
| `node`    | packages  | Node.js packages                   | `npm`, `pnpm`, `bun`          |
| `node`    | projects  | Node.js project sync               | `npm`, `pnpm`, `yarn`, `bun`  |
| `deno`    | packages  | Deno packages                      | `deno`                        |
| `deno`    | projects  | Deno project sync                  | `deno`                        |

### Preferences

Override the default installer selection per ecosystem:

```json
{
    "preferences": {
        "python": "uv"
    }
}
```

## Sync Strategies

| Strategy  | Flag                    | Behaviour                                                    |
| --------- | ----------------------- | ------------------------------------------------------------ |
| `minimal` | (default)               | Install missing packages; leave already-installed ones alone |
| `latest`  | `--strategy latest`     | Upgrade every package to its latest allowed version          |
| `exact`   | `--strategy exact`      | Ensure each package satisfies the declared constraint        |

## Usage

Sync current directory:

```shell
porringer sync
```

Sync a specific directory:

```shell
porringer sync --path ./my-project
```

## Dry Run (Preview)

Preview actions without executing:

```shell
porringer sync --dry-run
porringer sync --dry-run --path ./my-project
```

Dry-run mode previews *all* actions, including project-sync commands.
Project-sync backends that support `--dry-run` (e.g. `pdm`, `uv`) receive the
flag so they can report what they *would* do without modifying the environment.

## Execution Phases

The sync engine processes actions in five ordered phases:

| Phase | Kind        | Description                                               |
| ----- | ----------- | --------------------------------------------------------- |
| 1     | `runtimes`  | Install language runtimes (e.g. Python via `pim`/`pyenv`) |
| 2a    | `packages`  | Install packages (e.g. `pipx` via `pip`)                  |
| 2b    | `tools`     | Install CLI tools (e.g. `pdm` via `pipx`)                 |
| 3     | `projects`  | Synchronise project dependencies from lock files          |
| 4     | `post_sync` | Run post-sync commands (e.g. `pdm install`)               |

Between Phases 2a and 2b the engine **re-discovers available plugins** so that
tools installed during Phase 2a (e.g. `pipx`) can be used as installers in
Phase 2b.  This enables **deferred tool resolution** — a tool action whose
installer is not yet available at preview time is created with
`installer=None` and resolved just before execution.

### Bootstrap Chain Example

A single manifest can bootstrap an entire toolchain from scratch:

```json
{
    "version": "1",
    "runtimes": { "python": ["3.14"] },
    "packages": { "python": [{ "name": "pipx" }] },
    "tools": { "python": [{ "name": "pdm" }] },
    "post_sync": ["pdm install"]
}
```

Execution order: `pim`/`pyenv` installs Python 3.14 → `pip` installs `pipx` →
re-discovery finds `pipx` → `pipx` installs `pdm` → `pdm install` syncs
project deps.

See `examples/python-bootstrap/` for a ready-to-use example.

## Upgrade strategy

```shell
porringer sync --strategy latest
porringer sync --strategy exact --path ./my-project
```

## All Cached Directories

Sync all cached directories:

```shell
porringer sync --all
```

## Project Directory Override

By default, project-sync backends and post-sync commands run in the manifest's
parent directory.  Use `--project-dir` to point them elsewhere:

```shell
porringer sync --path manifest.json --project-dir ./my-project
```

## Standalone Manifests (No Project)

When consuming a manifest that has no associated project (e.g. a manifest
downloaded from a URL), use the API with `project_directory=False` to install
package-level requirements and skip project-sync backends:

```python
params = SetupParameters(paths=manifest_path, project_directory=False)
results = api.sync.run(params)

# Inspect which actions were skipped and why
for skip in results.skips:
    print(f"Skipped: {skip.action.description} — {skip.skip_reason.value}: {skip.message}")
```

**Note:** `project_directory=False` skips only **project-sync** actions (Phase 3).
Post-sync commands (Phase 4) still execute — they are independent of whether a
project directory exists.  Project-sync actions are reported as *skipped* (not
failed), so `results.success` remains `True`.  Use `results.skips` or
`results.total_skipped` to detect and act on skipped actions.

## API Usage

```python
import asyncio
from porringer.api import API
from porringer.schema import LocalConfiguration, SetupParameters

api = API(LocalConfiguration())

# Full sync (project + packages)
results = api.sync.run(SetupParameters(paths=project_path))

# Packages only — skip project backends and post-sync commands
results = api.sync.run(SetupParameters(paths=manifest_path, project_directory=False))

# Override project directory (manifest and project in different locations)
results = api.sync.run(
    SetupParameters(paths=manifest_path, project_directory=project_path)
)

# Execute with streaming progress
async def run():
    async for event in api.sync.execute_stream(SetupParameters(paths=project_path)):
        print(event.kind, getattr(event.action, 'description', 'manifest loaded'))

asyncio.run(run())
```
