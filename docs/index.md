# Porringer

Porringer is a CLI and API that discovers and orchestrates multiple package managers through a plugin system.

## Features

- Plugin-based package manager abstraction
- Platform-aware manifest processing
- Backend resolution — manifest declares *what* (e.g. `python`), Porringer picks the best available installer (`uv`, `pip`, …)
- Sync strategies: minimal, latest, or exact
- Secure downloads with hash verification
- Dry-run preview before execution
- Support for `porringer.json` and `pyproject.toml` manifests

## Commands

| Command | Description |
|---------|-------------|
| `porringer plugin list` | List available plugins |
| `porringer sync` | Synchronise environment from manifest |
| `porringer check` | Check for updates from various sources |
| `porringer download` | Download files with hash verification |
| `porringer self update` | Update Porringer |

## Documentation

- [Sync Command](sync.md) - Environment synchronisation from manifests
- [Check Command](check.md) - Update checking
- [Download Command](download.md) - File downloads with verification
- [Example: Python Dev Environment](../examples/python-dev/README.md)
