# Node.js Development Environment Example

This example demonstrates using Porringer to set up a Node.js / Deno development environment with common tools.

## Manifest Overview

The `porringer.json` manifest declares the desired environment using kind sections (`packages`, `projects`), each keyed by ecosystem. Porringer resolves each ecosystem to the best available installer at runtime.

- **`packages.node`** (resolved to `pnpm`, `npm`, or `bun`): Global npm packages
  - `typescript` – TypeScript compiler
  - `@biomejs/biome` – Fast linter and formatter for JS/TS
  - `tsx` – TypeScript execution engine
- **`packages.deno`** (resolved to `deno`): Globally installed Deno scripts/tools
  - `jsr:@std/cli` – Deno standard-library CLI helpers
- **`projects.node`** (resolved to `pnpm`, `npm`, `yarn`, or `bun`): Project dependency sync (`install`)
- **`projects.deno`** (resolved to `deno`): Project dependency sync for Deno projects

## Usage

### Preview what will happen

```shell
porringer sync --path examples/node-dev --dry-run
```

### Execute with confirmation

```shell
porringer sync --path examples/node-dev
```

## Notes

Package specifiers use each ecosystem's native syntax:

- **npm-style**: `typescript`, `@biomejs/biome`, `lodash@^4.0.0`, `@types/node@latest`
- **Deno-style**: `jsr:@std/cli`, `npm:chalk`, bare names (auto-prefixed with `npm:`)

Porringer passes constraints to the underlying tool verbatim — no version wrangling.
