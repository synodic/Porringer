# Check Command

The check command queries installed plugins to see if package updates are available. Each plugin uses its native tooling to check for updates.

## Basic Usage

Check all plugins for available updates:

```shell
porringer check
```

## Filtering by Plugin

Check specific plugins only:

```shell
porringer check --plugin pip
porringer check --plugin pip --plugin pipx
```

## Include Pre-releases

```shell
porringer check --prereleases
```

## Options

| Option         | Short | Description                                      |
| -------------- | ----- | ------------------------------------------------ |
| `--plugin`     | `-p`  | Plugin(s) to check. Omit to check all.           |
| `--prereleases`|       | Include pre-release versions in results.         |

## API Usage

```python
from porringer.backend.builder import Builder
from porringer.core.plugin_schema.environment import CheckUpdatesParameters

builder = Builder()

# Build plugin environments
plugin_infos = builder.find_environments()
environments = builder.build_environments(plugin_infos)

# Check each plugin
for env in environments:
    params = CheckUpdatesParameters(packages=[], include_prereleases=False)
    updates = env.check_updates(params)

    for pkg in updates:
        print(f"{pkg.name}: update available to {pkg.version}")
```

## Plugin Implementation

Plugins can implement the `check_updates` method to provide update checking:

```python
from porringer.core.plugin_schema.environment import (
    Environment,
    CheckUpdatesParameters,
)
from porringer.core.schema import Package

class MyPluginEnvironment(Environment):
    def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        # Use native tooling to check for updates
        # Return packages that have updates available
        return []
```

Plugins that don't implement `check_updates` will return an empty list by default.
