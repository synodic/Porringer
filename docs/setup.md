---
icon: lucide/play
---

# Setup

Install Porringer, then run `preview` before the first `install`. Preview is read-only, so it is the safest way to confirm which plugins, packages, runtimes, repositories, and project-install actions Porringer sees.

## Install

=== "pipx"

    ```shell
    pipx install porringer
    porringer --version
    ```

=== "uv"

    ```shell
    uv tool install porringer
    porringer --version
    ```

=== "pip"

    ```shell
    python -m pip install porringer
    porringer --version
    ```

## First Run

Point Porringer at one of the repository examples:

```shell
porringer preview examples/python-dev
porringer install examples/python-dev --strategy minimal
```

Use `preview` for the inspection step in any workflow. It reports selected plugins, actions, diagnostics, native command previews, and stable action IDs without installing packages or running project-install commands.

## Manifest Locations

Porringer looks for manifests in this order:

1. `porringer.json` in the target directory.
2. `[tool.porringer]` in `pyproject.toml`.

You can pass a directory, a manifest file, an HTTPS setup profile, or a `porringer://` install link to commands that inspect or run setup.

## Development Checkout

For repository development, install the project with PDM and run the standard validation loop:

```shell
pdm install
pdm run test
pdm run generate
```

See [Development](development.md) for smoke tests, trace artifacts, and environment diagnostics.
