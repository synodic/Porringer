# Setup Command

The setup command orchestrates project initialization from a manifest file.

## Manifest Files

Porringer looks for manifests in this order:

1. `porringer.json` - JSON manifest file
2. `pyproject.toml` - `[tool.porringer]` section

## Manifest Schema

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Schema version (currently `"1"`) |
| `prerequisites` | array | Plugins that must be available |
| `packages` | object | Packages to install per plugin (plugin name → package list) |
| `post_install` | array | Commands to run after installation |

## CLI Usage

Preview actions (default):

```shell
porringer setup [PATH]
```

Execute with confirmation:

```shell
porringer setup run [PATH]
```

Execute without confirmation:

```shell
porringer setup run --yes [PATH]
```

## API Usage

```python
from porringer.api import API
from porringer.schema import APIParameters, LocalConfiguration, SetupParameters

api = API(LocalConfiguration(), APIParameters(logger))

# Preview
preview = api.setup.preview(SetupParameters(path=project_path))

# Execute
results = api.setup.execute(preview.actions, SetupParameters(path=project_path))
```
