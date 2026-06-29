# Python Development Environment Example

This example shows a small Python development environment managed by Porringer. It installs common linting, formatting, type-checking, testing, and project-management tools.

## What the Manifest Declares

The `porringer.json` manifest groups entries by kind and ecosystem. Porringer resolves each ecosystem to an available installer at runtime, then detects project sync from repository files when a project plugin has ownership evidence.

| Manifest section | Resolves to | Installs |
| --- | --- | --- |
| `packages.python` | `uv` or `pip` | `ruff`, `pyrefly`, `pytest`, `pytest-asyncio` |
| `tools.python` | `pipx` | `pdm` |

These packages mirror the repository's own `lint` and `test` dependency groups in `pyproject.toml`. If you run preview from a matching development environment, the package actions should report as satisfied.

## Usage

Preview the plan without changing the environment:

```shell
porringer preview examples/python-dev
```

Run the setup with confirmation:

```shell
porringer install examples/python-dev
```

Skip confirmation in a scripted flow:

```shell
porringer install examples/python-dev --yes
```

Upgrade every package to the latest allowed version:

```shell
porringer install examples/python-dev --strategy latest
```

## Embedded pyproject Manifest

The same manifest can live in `pyproject.toml`:

```toml
[tool.porringer]
version = "1"

[tool.porringer.packages]
python = ["ruff", "pyrefly", "pytest", "pytest-asyncio"]

[tool.porringer.tools]
python = ["pdm"]

[tool.porringer.preferences]
python = "uv"
```
