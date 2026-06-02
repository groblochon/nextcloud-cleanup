# Nextcloud S3 Storage Cleanup (Python Version)

This script helps clean up leftover files on Nextcloud when using S3 object storage as the primary backend. It addresses issues where Nextcloud fails to clean up orphaned chunks from failed or canceled uploads ([#30762](https://github.com/nextcloud/server/issues/30762), [#29841](https://github.com/nextcloud/server/issues/29841)).

The script has been rewritten in **Python** for better maintainability and versatility.

## Features

- **Upload Cleanup (Default)**: Rapidly cleans up orphaned upload chunks older than a specified grace period.
- **Full Integrity Scan (`--scan-all`)**: Scans all files in the database and verifies their physical existence on S3. If an object is missing on S3 but present in the database, the database entry is removed.
- **Dry-Run Mode**: See what would be deleted without taking any action.
- **Docker Ready**: Easy deployment using Docker or Docker Compose.

## Prerequisites

- Nextcloud with S3 primary storage.
- MySQL or MariaDB database.
- Python 3.11+ (if running locally) or Docker.

## Configuration

Copy the `.env.example` file to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_HOST` | Database server address | |
| `DATABASE_USER` | Database username | |
| `DATABASE_PASSWORD` | Database password | |
| `DATABASE_NAME` | Nextcloud database name | |
| `AWS_ENDPOINT` | S3 endpoint URL | |
| `AWS_ACCESS_KEY_ID` | S3 access key | |
| `AWS_SECRET_ACCESS_KEY` | S3 secret key | |
| `AWS_BUCKET` | S3 bucket name | |
| `DELETION_GRACE_PERIOD` | Age of files to clean (seconds) | `86400` (24h) |
| `NEXTCLOUD_FILENAME_PATTERN` | Pattern for S3 objects | `urn:oid:%d` |

## Usage

### Using Docker Compose (Recommended)

1. **Test your configuration (Dry Run)**:
   ```bash
   docker compose run --build cleanup --dry-run
   ```

2. **Run the standard cleanup (Uploads)**:
   ```bash
   docker compose run --build cleanup
   ```

3. **Perform a full integrity scan**:
   This is useful for fixing "Failed to read object" errors.
   ```bash
   docker compose run --build cleanup --scan-all
   ```

### Running Locally

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the script:
```bash
python clean.py --scan-all --dry-run
```

## Troubleshooting

### Connection Errors
If you are running the database on the host machine and the container cannot reach it, the `docker-compose.yml` is configured with `network_mode: host` to allow easy LAN/Localhost access.

### "Commands out of sync"
This version of the script uses buffered cursors and a two-pass processing logic to avoid MySQL synchronization issues.
