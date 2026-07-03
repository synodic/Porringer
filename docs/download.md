---
icon: lucide/download
---

# Download API

Porringer exposes a non-blocking download helper for applications that need to
fetch a file and optionally verify its hash or size before treating it as
usable. It is used internally to resolve setup profiles and remote manifests,
and is available to downstream callers through `API.download`.

Porringer does not ship a standalone `download` CLI command — file downloads
belong to the tools and workflows that own them. Use `API.download` from an
application, or declare the artifacts your setup needs in a manifest.

## API Usage

```python
import asyncio
from pathlib import Path

from porringer.api import API
from porringer.schema import DownloadParameters

params = DownloadParameters(
    url='https://example.com/file.zip',
    destination=Path('./file.zip'),
    expected_hash='sha256:abc123...',
)

result = asyncio.run(API.download(params))
print(f'Download success: {result.success}')
```

## Verification

Pass `expected_hash` to verify the downloaded file's digest, or `expected_size`
to verify its byte count. Supported hash algorithms:

- `sha256`
- `sha512`

```python
params = DownloadParameters(
    url='https://example.com/file.zip',
    destination=Path('./file.zip'),
    expected_hash='sha256:abc123...',
    expected_size=1048576,
)
```

## Timeout

The default timeout is 300 seconds. Increase it for large files or slow
networks:

```python
params = DownloadParameters(
    url='https://example.com/large-file.zip',
    destination=Path('./file.zip'),
    timeout=600,
)
```

## Progress

`API.download` accepts an optional progress callback that receives the number of
bytes downloaded and the total size (when known):

```python
def on_progress(downloaded: int, total: int | None) -> None:
    if total:
        print(f'{downloaded / total:.0%}')


result = asyncio.run(API.download(params, on_progress))
```

## Progress Callback

Programmatic callers can provide a progress callback:

```python
import asyncio


def progress(downloaded: int, total: int | None) -> None:
    if total:
        percent = (downloaded / total) * 100
        print(f'Progress: {percent:.1f}%')


result = asyncio.run(API.download(params, progress_callback=progress))
```
