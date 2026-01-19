# Update Command

The update command checks for updates from various sources and downloads files with hash verification.

## Update Sources

Porringer supports three update sources:

| Source | Description | Required Parameter |
|--------|-------------|-------------------|
| `github` | GitHub Releases API | `--repo owner/repo` |
| `pypi` | PyPI JSON API | `--package name` |
| `custom` | Custom JSON URL | `--url https://...` |

## Check for Updates

### GitHub Releases

```shell
porringer update check --source github --repo owner/repo --current 1.0.0
```

With authentication (for rate limits):

```shell
porringer update check --source github --repo owner/repo --current 1.0.0 --github-token $GITHUB_TOKEN
```

### PyPI

```shell
porringer update check --source pypi --package requests --current 2.28.0
```

### Custom URL

The custom URL should return JSON with this schema:

```json
{
  "version": "2.0.0",
  "download_url": "https://example.com/v2.0.0.zip",
  "release_notes_url": "https://example.com/changelog",
  "published_at": "2024-01-15T10:00:00Z"
}
```

```shell
porringer update check --source custom --url https://example.com/version.json --current 1.0.0
```

### Include Pre-releases

```shell
porringer update check --source github --repo owner/repo --current 1.0.0 --prereleases
```

## Download Files

Basic download:

```shell
porringer update download https://example.com/file.zip ./file.zip
```

With hash verification:

```shell
porringer update download https://example.com/file.zip ./file.zip --hash sha256:abc123...
```

With size verification:

```shell
porringer update download https://example.com/file.zip ./file.zip --size 1048576
```

## Progress Callback

For programmatic usage, you can provide a progress callback:

```python
def progress(downloaded: int, total: int | None) -> None:
    if total:
        percent = (downloaded / total) * 100
        print(f"Progress: {percent:.1f}%")

result = api.update.download(params, progress_callback=progress)
```
