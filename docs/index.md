# Porringer

Porringer is a CLI and API that discovers and orchestrates multiple package managers through a plugin system.

## Features

- Plugin-based package manager abstraction
- Platform-aware manifest processing
- Secure downloads with hash verification
- Dry-run preview before execution
- Support for `porringer.json` and `pyproject.toml` manifests

## Commands

| Command | Description |
|---------|-------------|
| `porringer plugin list` | List available plugins |
| `porringer install` | Setup project from manifest |
| `porringer check` | Check for updates from various sources |
| `porringer download` | Download files with hash verification |
| `porringer self update` | Update Porringer |

## Documentation

- [Install Command](install.md) - Project setup from manifests
- [Check Command](check.md) - Update checking
- [Download Command](download.md) - File downloads with verification
- [Example: Python Dev Environment](../examples/python-dev/README.md)
