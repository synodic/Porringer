# Download Command

The download command downloads files with optional hash verification.

## Basic Download

```shell
porringer download https://example.com/file.zip ./file.zip
```

## Hash Verification

Verify the file hash after download:

```shell
porringer download https://example.com/file.zip ./file.zip --hash sha256:abc123...
```

Supported hash algorithms:

- `sha256`
- `sha512`

## Size Verification

Verify file size after download:

```shell
porringer download https://example.com/file.zip ./file.zip --size 1048576
```

## Timeout

Set download timeout (default: 300 seconds):

```shell
porringer download https://example.com/large-file.zip ./file.zip --timeout 600
```

## API Usage

```python
from porringer.api import API
from porringer.schema import LocalConfiguration, DownloadParameters

api = API(LocalConfiguration())

params = DownloadParameters(
    url="https://example.com/file.zip",
    destination=Path("./file.zip"),
    expected_hash="sha256:abc123...",
)

result = api.sync.download(params)
print(f"Download success: {result.success}")
```

## Progress Callback

For programmatic usage, you can provide a progress callback:

```python
def progress(downloaded: int, total: int | None) -> None:
    if total:
        percent = (downloaded / total) * 100
        print(f"Progress: {percent:.1f}%")

result = api.sync.download(params, progress_callback=progress)
```
