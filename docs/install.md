# Install Command

The install command sets up development environments from manifest files.

## Manifest Files

Porringer looks for manifests in this order:

1. `porringer.json` - JSON manifest file
2. `pyproject.toml` - `[tool.porringer]` section

## Manifest Schema

| Field           | Type   | Description                                                           |
| --------------- | ------ | --------------------------------------------------------------------- |
| `version`       | string | Schema version (currently `"1"`)                                      |
| `prerequisites` | array  | Plugins that must be available (with optional platform filtering)     |
| `packages`      | object | Packages to install per plugin (plugin name → package list)           |
| `post_install`  | array  | Commands to run after installation                                    |

### Prerequisite Schema

Each item in `prerequisites` has:

| Field       | Type   | Description                                                              |
| ----------- | ------ | ------------------------------------------------------------------------ |
| `plugin`    | string | The plugin name that must be available (e.g., `pip`, `pipx`, `winget`) |
| `platforms` | array  | Optional platform filter (e.g., `["win32", "darwin", "linux"]`)         |

If `platforms` is omitted or empty, the prerequisite applies to all platforms.

## Execute Installation

Run in current directory:

```shell
porringer install
```

Run in specific directory:

```shell
porringer install --path ./my-project
```

## Dry Run (Preview)

Preview actions without executing:

```shell
porringer install --dry-run
porringer install --dry-run --path ./my-project
```

## All Cached Directories

Run on all cached directories:

```shell
porringer install --all
```

## API Usage

```python
import asyncio
from porringer.api import API
from porringer.schema import LocalConfiguration, SetupParameters

api = API(LocalConfiguration())

# Preview (dry run)
preview = api.update.preview_batch(SetupParameters(paths=project_path))

# Execute with streaming progress
async def run():
    async for event in api.update.execute_stream(preview, SetupParameters(paths=project_path)):
        print(event.kind, event.action.description)

asyncio.run(run())
```
