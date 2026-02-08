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
| `state`       | object | Desired package state per backend (backend name → package list)               |
| `preferences` | object | Optional installer overrides per backend (e.g. `{"python": "uv"}`)           |
| `post_sync`   | array  | Commands to run after synchronisation                                         |
| `name`        | string | Optional human-readable project name                                          |
| `description` | string | Optional short description                                                    |
| `author`      | string | Optional author or organisation name                                          |
| `url`         | string | Optional project URL                                                          |

### State

The `state` object maps **backend identifiers** to lists of packages. Porringer resolves each backend to the best available installer plugin at runtime.

Backends fall into two categories:

- **Package backends** (e.g. `python`, `node`, `system`) install individual packages.
- **Project backends** (e.g. `python-project`, `node-project`) synchronise an entire project's dependencies from its lock file.

Well-known backends:

| Backend            | Type    | Description                        | Default installers           |
| ------------------ | ------- | ---------------------------------- | ----------------------------- |
| `python`           | package | Python packages                    | `uv`, `pip`                   |
| `python-tool`      | package | CLI tools in isolated environments | `pipx`                        |
| `system`           | package | OS-level packages                  | `brew`, `apt`, `winget`       |
| `node`             | package | Node.js packages                   | `npm`                         |
| `python-runtime`   | package | Python runtimes                    | `pim`, `pyenv`                |
| `python-project`   | project | Python project sync                | `uv`, `pdm`, `poetry`         |
| `node-project`     | project | Node.js project sync               | `npm`, `pnpm`, `bun`          |
| `deno-project`     | project | Deno project sync                  | `deno`                        |

### Preferences

Override the default installer selection per backend:

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
