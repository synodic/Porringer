# Python Development Environment Example

This example demonstrates using Porringer to set up a Python development environment with common linting, formatting, and testing tools.

## Manifest Overview

The `porringer.json` manifest declares the desired environment using kind sections (`packages`, `tools`, `projects`, `runtimes`), each keyed by ecosystem. Porringer resolves each ecosystem to the best available installer at runtime.

- **`packages.python`** (resolved to `uv` or `pip`): Development tools installed in the current environment
  - `ruff` - Fast Python linter and formatter
  - `pyrefly` - Python type checker
  - `pytest` - Testing framework
  - `pytest-cov` - Coverage plugin for pytest
- **`tools.python`** (resolved to `pipx`): CLI tools installed in isolated environments
  - `pdm` - Python project manager

## Usage

### Preview what will happen

```shell
porringer sync --path examples/python-dev --dry-run
```

### Execute with confirmation

```shell
porringer sync --path examples/python-dev
```

### Execute without confirmation (non-interactive)

```shell
porringer sync --path examples/python-dev --yes
```

### Upgrade all packages to latest

```shell
porringer sync --path examples/python-dev --strategy latest
```

## Alternative: pyproject.toml

You can also embed this manifest in a `pyproject.toml` file:

```toml
[tool.porringer]
version = "1"

[tool.porringer.packages]
python = ["ruff", "pyrefly", "pytest", "pytest-cov"]

[tool.porringer.tools]
python = ["pdm"]

[tool.porringer.preferences]
python = "uv"  # optional: prefer uv over pip
```

```
