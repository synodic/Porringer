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
downloaded from a URL), use the API with `project_directory=False` to install only
package-level requirements and skip project backends:

```python
params = SetupParameters(paths=manifest_path, project_directory=False)
preview = api.sync.preview_batch(params)
results = execute_stream(preview, params)

# Inspect which actions were skipped and why
for skip in results.skips:
    print(f"Skipped: {skip.action.description} — {skip.skip_reason.value}: {skip.message}")
```

Project-sync and post-sync actions are reported as *skipped* (not failed),
so `results.success` remains `True`.  Use `results.skips` or
`results.total_skipped` to detect and act on skipped actions.

## API Usage

```python
import asyncio
from porringer.api import API
from porringer.schema import LocalConfiguration, SetupParameters

api = API(LocalConfiguration())

# Full sync (project + packages)
preview = api.sync.preview_batch(SetupParameters(paths=project_path))

# Packages only — skip project backends and post-sync commands
preview = api.sync.preview_batch(SetupParameters(paths=manifest_path, project_directory=False))

# Override project directory (manifest and project in different locations)
preview = api.sync.preview_batch(
    SetupParameters(paths=manifest_path, project_directory=project_path)
)

# Execute with streaming progress
async def run():
    async for event in api.sync.execute_stream(preview, SetupParameters(paths=project_path)):
        print(event.kind, event.action.description)

asyncio.run(run())
```
