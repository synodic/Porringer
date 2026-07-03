# Node.js Development Environment Example

This example shows a Node.js development environment managed by Porringer. It installs common global packages and lets the Node project plugin sync project dependencies from `package.json`.

## What the Manifest Declares

| Manifest section | Resolves to | Installs or runs |
| --- | --- | --- |
| `packages.node` | `pnpm` or `npm` | `typescript`, `@biomejs/biome`, `tsx` |
| project install | `npm` or `pnpm` project plugin | The project's native install command |

Porringer passes Node package specifiers to the selected tool using that ecosystem's native syntax.

## Usage

Preview the plan without changing the environment:

```shell
porringer preview examples/node-dev
```

Run the setup with confirmation:

```shell
porringer install examples/node-dev
```

Skip confirmation in a scripted flow:

```shell
porringer install examples/node-dev --yes
```

## Package Specifiers

Use normal npm-style specifiers:

- `typescript`
- `@biomejs/biome`
- `lodash@^4.0.0`
- `@types/node@latest`

Porringer passes version constraints to the underlying tool. The selected Node package manager owns the exact interpretation.
