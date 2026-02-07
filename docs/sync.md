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

Well-known backends:

| Backend          | Description                          | Default installers       |
| ---------------- | ------------------------------------ | ------------------------ |
| `python`         | Python packages                      | `uv`, `pip`              |
| `python-tool`    | CLI tools in isolated environments   | `pipx`                   |
| `system`         | OS-level packages                    | `brew`, `apt`, `winget`  |
| `node`           | Node.js packages                     | `npm`                    |
| `python-runtime` | Python runtimes                      | `pim`                    |

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

## API Usage

```python
import asyncio
from porringer.api import API
from porringer.schema import LocalConfiguration, SetupParameters

api = API(LocalConfiguration())

# Preview (dry run)
preview = api.sync.preview_batch(SetupParameters(paths=project_path))

# Execute with streaming progress
async def run():
    async for event in api.sync.execute_stream(preview, SetupParameters(paths=project_path)):
        print(event.kind, event.action.description)

asyncio.run(run())
```
