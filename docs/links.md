---
icon: lucide/link
---

# Install Links

Porringer can be opened from a `porringer://` link, for example from a web page or storefront app:

```text
porringer://profile?url=https://example.com/profile.json&sha256=<digest>
```

A link is a way to find and preview a setup profile. It is not permission to install anything.

## What a Link Does

- Opens a read-only preview of the referenced setup profile through `porringer open`.
- Shows the packages, tools, runtimes, repositories, and project-install actions that would run.
- Displays the source origin before any action is taken.
- Stops after preview. A person must run or confirm `porringer install <link>` before anything executes.

## What a Link Cannot Do

- It cannot install, upgrade, or remove anything on its own.
- It cannot carry an auto-confirm flag.
- It cannot bypass the confirmation behavior of the CLI or application that opens it.

## Verification

| Layer | Guarantee | How |
| --- | --- | --- |
| Layer | Guarantee | How |
| --- | --- | --- |
| Transport | The profile is fetched over a secure channel. | HTTPS is required; other schemes are rejected. |
| Integrity | The profile bytes match the link. | The `sha256` value pins the profile and is verified on download. Referenced manifests can pin their own `expected_hash`. |
| Origin | The person previewing can see who offered the setup. | The source host is shown before any action. |

## Link Registration

The `porringer://` scheme is declared by packaged application builds. The operating system and store mediate the handler registration. Porringer does not edit system handler settings at runtime.

| Platform | Declared in |
| --- | --- |
| Windows (MSIX / Microsoft Store) | `windows.protocol` extension in the package manifest |
| macOS (App Store / notarized) | `CFBundleURLTypes` in `Info.plist` |
| Linux (Flatpak / Snap) | `x-scheme-handler/porringer` in the `.desktop` entry |

## Why Links Only Preview

- A click should never be an install. Running commands on a machine requires a deliberate action.
- Links are wired to inspection first, so a link can show a plan that the user declines.
- Project-install actions remain plugin-owned. Links can preview those actions, but installed plugins still select them from repository evidence.
- Integrity is not the same as trust. HTTPS and hashes prove bytes are intact; they do not prove the author is trustworthy.

See [Scope and Non-goals](scope.md) for the boundary of what Porringer does, and
[Preview Command](preview.md) for the preview that every link relies on.
