# Porringer

Porringer is a CLI and API that discovers and orchestrates multiple package managers through a plugin system.

## Features

- Plugin-based package manager abstraction
- Secure downloads with hash verification
- Dry-run preview before execution
- Support for `porringer.json` and `pyproject.toml` manifests

## Commands

| Command | Description |
|---------|-------------|
| `porringer plugin list` | List available plugins |
| `porringer setup` | Setup project from manifest |
| `porringer update check` | Check for updates from various sources |
| `porringer update download` | Download files with hash verification |
| `porringer self update` | Update Porringer |

## Documentation

- [Setup Command](setup.md) - Project setup from manifests
- [Update Command](update.md) - Update checking and downloads
