---
icon: lucide/download
---

# Download Command

Use `porringer download` to fetch a file and optionally verify its hash or size before treating it as usable.

## Basic Usage

```shell
porringer download https://example.com/file.zip ./file.zip
```

## Verification

Verify the file hash after download:

```shell
porringer download https://example.com/file.zip ./file.zip --hash sha256:abc123...
```

Supported hash algorithms:

- `sha256`
- `sha512`

Verify file size after download:

```shell
porringer download https://example.com/file.zip ./file.zip --size 1048576
```

## Timeout

The default timeout is 300 seconds. Increase it for large files or slow networks:

```shell
porringer download https://example.com/large-file.zip ./file.zip --timeout 600
```

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
