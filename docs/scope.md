---
icon: lucide/target
---

# Scope and Non-goals

Porringer is a cross-platform orchestrator for developer toolchains. It reads a declarative manifest and drives existing package managers and project tools through plugins.

The boundary is intentional: Porringer coordinates package, runtime, repository, and project setup. It does not replace the tools that actually install packages or configure operating systems.

## In Scope

- Declaring packages, CLI tools, language runtimes, and source repositories.
- Resolving installers per ecosystem through plugins.
- Resolving project sync through project plugins and repository evidence.
- Running setup in a deterministic phase order.
- Inspecting manifests and reporting structured diagnostics before execution.
- Supporting local manifests, HTTPS setup profiles, and hash-verified downloads.

## Non-goals

- **Machine or OS provisioning.** Porringer does not configure registry settings, shell themes, fonts, terminal profiles, services, or reboot choreography.
- **Platform-specific core behavior.** Core manifest semantics are platform-neutral. Platform specifics belong in plugins.
- **Replacing substrate package managers.** Porringer declares what should exist. Tools such as `apt`, `winget`, `pip`, `npm`, and `pdm` still do the installation work.
- **Provisioning WSL distributions.** Porringer does not create or configure WSL distributions.
- **Managing remote machines.** Porringer targets the current machine or session. It is not remote host orchestration.

## Project Sync Policy

Project-local dependency sync belongs to project-environment plugins, not arbitrary manifest commands. Plugins identify relevant projects from marker files, lock files, and tool-specific configuration. Porringer then selects one project-sync owner per ecosystem.

Custom imperative setup remains outside the core manifest. If a workflow needs arbitrary shell commands, keep those commands in the project tool that owns them and let Porringer invoke the tool through its plugin.
