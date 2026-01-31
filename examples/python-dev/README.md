# Python Development Environment Example

This example demonstrates using Porringer to set up a Python development environment with common linting, formatting, and testing tools.

## Manifest Overview

The `porringer.json` manifest defines:

- **Prerequisites**: Ensures `pip` and `pipx` plugins are available
- **pip packages**: Development tools installed in the current environment
  - `ruff` - Fast Python linter
  - `pyrefly` - Static type checker
  - `pytest` - Testing framework
  - `pytest-cov` - Coverage plugin for pytest
- **pipx packages**: CLI tools installed in isolated environments
  - `pdm` - Python project manager

## Usage

### Preview what will happen

```shell
porringer setup examples/python-dev
```

### Execute with confirmation

```shell
porringer setup run examples/python-dev
```

### Execute without confirmation

```shell
porringer setup run --yes examples/python-dev
```

## Alternative: pyproject.toml

You can also embed this manifest in a `pyproject.toml` file:

```toml
[tool.porringer]
version = "1"

[[tool.porringer.prerequisites]]
plugin = "pip"

[[tool.porringer.prerequisites]]
plugin = "pipx"

[tool.porringer.packages]
pip = ["ruff", "pyrefly", "pytest", "pytest-cov"]
pipx = ["pdm"]
```
