# Rclone CCTV Multi-Remote Upload and Cleanup Script

A Python script for uploading CCTV/Shinobi recordings to multiple `rclone` remotes while also cleaning old or excessive files from remote camera folders.

The script is designed for setups where one local CCTV directory is copied, synced, or moved to several cloud/encrypted `rclone` remotes, for example Google Drive, Mega, OneDrive, SFTP, or other supported backends.

> [!WARNING]
> This script can delete files from your configured `rclone` remotes.
>
> Depending on your configuration, it can either:
>
> - delete directly/permanently where supported, or
> - use the backend/cloud provider trash behavior where supported.
>
> Direct delete mode may use options such as:
>
> ```bash
> --drive-use-trash=false
> ```
>
> That can bypass the cloud provider trash/recycle bin and may make deleted files unrecoverable.
>
> **You are fully responsible for all damage, data loss, account issues, misconfiguration, or other problems caused by using this software.**
>
> The author, contributors, and anyone sharing this script are **not responsible** for any loss or damage.
>
> Use this script at your own risk. Always test with non-important data first.

---

## Features

- Uploads CCTV/Shinobi recordings to multiple `rclone` remotes.
- Supports different upload commands per destination:
  - `copy`
  - `sync`
  - `move`
- Supports different upload options per remote.
- Cleans old files from remote camera folders.
- Can delete old files based on age.
- Can delete excess files based on:
  - maximum number of files
  - maximum folder size
  - or both
- Deletes oldest files first when enforcing `max_files` or `max_size`.
- Can choose direct delete or trash/backend-default delete per camera folder.
- Can optionally empty trash per upload remote with `rclone cleanup`.
- Prints a startup summary before cleanup/upload starts.
- Can run cleanup jobs, trash cleanup jobs, and upload jobs in parallel.
- Uses a lock file to prevent multiple script instances from running at the same time.
- Handles missing remote folders without failing the whole script.
- Streams upload output live so progress can be seen in terminal, cron logs, or systemd logs.

---

## What the Script Does

The script runs in this order:

1. **Startup summary**
   - Prints configured thread limits.
   - Prints all upload destinations.
   - Prints all configured camera cleanup rules.
   - Prints every generated cleanup target.
   - Shows whether each target will delete old files, delete excess files, use direct delete, use trash/default behavior, and so on.

2. **Remote camera folder cleanup**
   - For each configured upload remote and each configured camera folder, the script builds a full cleanup path.
   - It can delete files older than the configured age.
   - It can delete oldest files until `max_files` and/or `max_size` limits are satisfied.
   - Missing remote folders are skipped.

3. **Remote trash cleanup**
   - Runs `rclone cleanup` on upload remotes where `empty_trash=True`.
   - Skips remotes where `empty_trash=False`.
   - Unsupported `rclone cleanup` is treated as non-fatal.

4. **Upload**
   - Runs the configured upload command from the local CCTV folder to each configured remote.
   - Uploads happen in parallel according to `UPLOAD_THREADS`.

The script will **not upload new files** if cleanup or trash cleanup has a real failure.

---

## Requirements

- Linux system
- Python 3
- `rclone`
- Configured `rclone` remotes
- Working access to the local CCTV/Shinobi recording folder

Check that `rclone` is installed:

```bash
rclone version
```

Check your configured remotes:

```bash
rclone listremotes
```

---

## GitHub-Safe Example Layout

This README uses generic example names only.

Example local CCTV folder:

```bash
/path/to/local/CCTV
```

Example upload destinations:

```bash
Example-GoogleDrive-Encrypted:/CCTV
Example-Mega-Encrypted:/CCTV
Example-OneDrive-Encrypted:/CCTV
```

Example camera folders below each remote:

```bash
Home/Camera01
Home/Camera02
Garage/Camera03
Outdoor/Camera04
```

The script combines every upload remote with every camera folder.

Example generated cleanup targets:

```bash
Example-GoogleDrive-Encrypted:/CCTV/Home/Camera01
Example-GoogleDrive-Encrypted:/CCTV/Home/Camera02
Example-GoogleDrive-Encrypted:/CCTV/Garage/Camera03
Example-GoogleDrive-Encrypted:/CCTV/Outdoor/Camera04

Example-Mega-Encrypted:/CCTV/Home/Camera01
Example-Mega-Encrypted:/CCTV/Home/Camera02
Example-Mega-Encrypted:/CCTV/Garage/Camera03
Example-Mega-Encrypted:/CCTV/Outdoor/Camera04

Example-OneDrive-Encrypted:/CCTV/Home/Camera01
Example-OneDrive-Encrypted:/CCTV/Home/Camera02
Example-OneDrive-Encrypted:/CCTV/Garage/Camera03
Example-OneDrive-Encrypted:/CCTV/Outdoor/Camera04
```

With 3 upload remotes and 4 camera folders, the script processes 12 cleanup targets.

---

## Configuration

Configuration is currently done directly inside the Python script.

---

## Camera Folder Cleanup List

Example:

```python
RCLONE_DIRECTORIES = [
    RcloneDirectory(
        path="Home/Camera01",
        max_files=80,
        delete_old_files=True,
        delete_excess_files=True,
        delete_to_trash=False,
    ),
    RcloneDirectory(
        path="Home/Camera02",
        max_size="50G",
        delete_old_files=True,
        delete_excess_files=True,
        delete_to_trash=True,
    ),
    RcloneDirectory(
        path="Garage/Camera03",
        max_files=120,
        delete_old_files=False,
        delete_excess_files=True,
        delete_to_trash=False,
    ),
    RcloneDirectory(
        path="Outdoor/Camera04",
        max_size="30G",
        delete_old_files=True,
        delete_excess_files=False,
        delete_to_trash=True,
    ),
]
```

Each entry defines one camera folder below each remote destination.

| Option | Meaning |
|---|---|
| `path` | Camera folder path relative to the remote root |
| `max_files` | Maximum number of newest files to keep |
| `max_size` | Maximum total folder size to keep, for example `"30G"` or `"500M"` |
| `delete_old_files` | Whether to delete files older than `DELETE_MIN_AGE` |
| `delete_excess_files` | Whether to enforce `max_files` and/or `max_size` |
| `delete_to_trash` | Whether to use trash/backend-default delete behavior instead of direct-delete mode |

### Cleanup Examples

Keep only the newest 80 files and delete directly where supported:

```python
RcloneDirectory(
    path="Home/Camera01",
    max_files=80,
    delete_old_files=True,
    delete_excess_files=True,
    delete_to_trash=False,
)
```

Keep the folder under 50 GiB and use trash/backend-default behavior:

```python
RcloneDirectory(
    path="Home/Camera02",
    max_size="50G",
    delete_old_files=True,
    delete_excess_files=True,
    delete_to_trash=True,
)
```

Skip age-based deletion, but still enforce a maximum number of files:

```python
RcloneDirectory(
    path="Garage/Camera03",
    max_files=120,
    delete_old_files=False,
    delete_excess_files=True,
    delete_to_trash=False,
)
```

Delete old files by age, but do not enforce `max_files` or `max_size`:

```python
RcloneDirectory(
    path="Outdoor/Camera04",
    max_size="30G",
    delete_old_files=True,
    delete_excess_files=False,
    delete_to_trash=True,
)
```

---

## Upload Destinations

Example:

```python
UPLOAD_DIRECTORIES = [
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-GoogleDrive-Encrypted:/CCTV",
        upload_command="copy",
        empty_trash=True,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "4",
            "--exclude", "/Home/OldCamera/**",
        ],
    ),
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-Mega-Encrypted:/CCTV",
        upload_command="sync",
        empty_trash=False,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "4",
            "--exclude", "/Home/OldCamera/**",
        ],
    ),
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-OneDrive-Encrypted:/CCTV",
        upload_command="move",
        empty_trash=False,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "2",
            "--exclude", "/Home/OldCamera/**",
            "--dry-run",
        ],
    ),
]
```

| Option | Meaning |
|---|---|
| `local_path` | Local source folder to upload from |
| `remote_path` | Remote destination path |
| `upload_command` | Which `rclone` upload command to use: `copy`, `sync`, or `move` |
| `empty_trash` | Whether to run `rclone cleanup` for this remote |
| `copy_options` | Extra options passed to the selected `rclone` upload command |

> [!CAUTION]
> `upload_command="sync"` can delete files from the destination if they are not present in the source.
>
> `upload_command="move"` can delete local source files after successful transfer.
>
> For backup-style CCTV upload, `upload_command="copy"` is usually the safest default.

---

## Upload Command Choices

| Command | Behavior | Risk |
|---|---|---|
| `copy` | Copies new/changed files to the destination | Safest for backups |
| `sync` | Makes destination match source | Can delete remote files |
| `move` | Moves files to destination and removes local source files after transfer | Can delete local files |

The script validates the command against this list:

```python
ALLOWED_UPLOAD_COMMANDS = {
    "copy",
    "sync",
    "move",
}
```

---

## Delete Age

Age-based cleanup uses:

```python
DELETE_MIN_AGE = "31d"
```

This means files older than 31 days can be deleted when `delete_old_files=True`.

Examples:

```python
DELETE_MIN_AGE = "7d"
DELETE_MIN_AGE = "31d"
DELETE_MIN_AGE = "6M"
```

The exact supported duration format depends on `rclone`.

---

## Direct Delete vs Trash Delete

Per camera folder:

```python
delete_to_trash=False
```

means the script adds direct-delete options where supported, for example:

```bash
--drive-use-trash=false
```

Per camera folder:

```python
delete_to_trash=True
```

means the script does not add the direct-delete option. The backend/default trash behavior is used where supported.

| Option | Meaning |
|---|---|
| `delete_to_trash=False` | Direct/permanent delete where supported |
| `delete_to_trash=True` | Use backend/default trash behavior where supported |

> [!CAUTION]
> Direct delete can make files unrecoverable.
>
> Trash behavior depends on the rclone backend and cloud provider.

---

## Empty Trash / `rclone cleanup`

Per upload remote:

```python
empty_trash=True
```

means the script runs:

```bash
rclone cleanup RemoteName:/Path
```

Per upload remote:

```python
empty_trash=False
```

means the script skips `rclone cleanup` for that remote.

Some backends do not support `rclone cleanup`. The script treats unsupported cleanup as non-fatal.

---

## Thread Limits

```python
UPLOAD_THREADS = 3
CLEANUP_THREADS = 3
TRASH_CLEANUP_THREADS = 3
```

| Option | Meaning |
|---|---|
| `UPLOAD_THREADS` | Number of parallel upload jobs |
| `CLEANUP_THREADS` | Number of parallel camera-folder cleanup jobs |
| `TRASH_CLEANUP_THREADS` | Number of parallel trash-cleanup jobs |

Lower these values if your machine, disk, network, or cloud providers become overloaded.

> [!NOTE]
> `UPLOAD_THREADS` controls how many `rclone` upload processes run at once.
>
> `--transfers` controls how many files each `rclone` process transfers at once.
>
> Example:
>
> ```text
> UPLOAD_THREADS = 3
> --transfers 4
> ```
>
> This can create up to about 12 active file transfers.

---

## Startup Summary

Before doing cleanup or upload, the script prints a startup summary.

It shows:

- thread limits
- all upload destinations
- upload command for each destination
- whether each destination will empty trash
- all camera cleanup rules
- all generated cleanup targets
- delete-old-files setting
- delete-excess-files setting
- `max_files`
- `max_size`
- delete mode
- total number of upload destinations
- total number of camera rules
- total number of cleanup targets

This helps catch wrong paths before the script starts doing destructive operations.

---

## Lock File

```python
LOCK_FILE = Path("/var/lock/subsys/RcloneLockFile.run")
```

The lock file prevents multiple copies of the script from running at the same time.

If the script exits normally, the lock file is removed automatically.

If the system crashes or the script is killed forcefully, you may need to remove a stale lock manually:

```bash
sudo rm /var/lock/subsys/RcloneLockFile.run
```

Only remove the lock file after verifying that the script is not still running:

```bash
ps aux | grep rclone
ps aux | grep python
```

---

## Installation

Clone or copy the script to a safe location:

```bash
sudo mkdir -p /opt/rclone-cctv
sudo nano /opt/rclone-cctv/rclone_cctv_upload.py
```

Make it executable:

```bash
sudo chmod +x /opt/rclone-cctv/rclone_cctv_upload.py
```

Edit the paths and remotes inside the script before running it.

---

## First-Time Safety Test

Before using the script on important data, test your `rclone` remotes manually.

List a remote:

```bash
rclone lsf Example-OneDrive-Encrypted:/CCTV
```

Test upload to a temporary folder:

```bash
rclone copy /path/to/test/files Example-OneDrive-Encrypted:/CCTV-Test --dry-run
```

Test delete behavior on a temporary folder only:

```bash
rclone delete Example-OneDrive-Encrypted:/CCTV-Test --min-age 31d --dry-run
```

Test size/listing behavior:

```bash
rclone lsf --files-only --format tsp --separator $'\t' Example-OneDrive-Encrypted:/CCTV-Test
```

> [!IMPORTANT]
> The script does not currently have a global `--dry-run` argument.
>
> For testing, temporarily add `--dry-run` to relevant `copy_options`, or temporarily add it to the delete commands in the script.

---

## Running the Script

Run manually:

```bash
sudo /opt/rclone-cctv/rclone_cctv_upload.py
```

Or:

```bash
sudo python3 /opt/rclone-cctv/rclone_cctv_upload.py
```

---

## Example Cron Job

Run every hour:

```bash
sudo crontab -e
```

Add:

```cron
0 * * * * /usr/bin/python3 /opt/rclone-cctv/rclone_cctv_upload.py >> /var/log/rclone-cctv.log 2>&1
```

View logs:

```bash
tail -f /var/log/rclone-cctv.log
```

---

## Example systemd Service

Create the service:

```bash
sudo nano /etc/systemd/system/rclone-cctv.service
```

```ini
[Unit]
Description=Rclone CCTV Upload and Cleanup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/rclone-cctv/rclone_cctv_upload.py
```

Create a timer:

```bash
sudo nano /etc/systemd/system/rclone-cctv.timer
```

```ini
[Unit]
Description=Run Rclone CCTV Upload and Cleanup every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rclone-cctv.timer
```

Check timer status:

```bash
systemctl list-timers rclone-cctv.timer
```

View logs:

```bash
journalctl -u rclone-cctv.service -f
```

---

## Important Safety Notes

### This Script Deletes Remote Files

The script performs remote cleanup before upload. If paths are wrong, it may delete files from the wrong remote folder.

Carefully check:

```python
RCLONE_DIRECTORIES
UPLOAD_DIRECTORIES
DELETE_MIN_AGE
delete_to_trash
empty_trash
upload_command
```

### Path Mistakes Can Be Dangerous

This is specific:

```bash
Example-OneDrive-Encrypted:/CCTV/Home/Camera01
```

This is much broader:

```bash
Example-OneDrive-Encrypted:/
```

A wrong path can cause unwanted cleanup or deletion.

### `sync` Can Delete Remote Files

If you use:

```python
upload_command="sync"
```

then files that do not exist in the source can be deleted from the destination.

### `move` Can Delete Local Files

If you use:

```python
upload_command="move"
```

then files may be removed from the local source after successful transfer.

### Direct Delete Can Be Permanent

If you use:

```python
delete_to_trash=False
```

then the script may bypass trash/recycle bin behavior where supported.

### Retention Uses Modified Time

For `max_files` and `max_size`, the script gets file entries using `rclone lsf` with modified time and size information.

It deletes the oldest files first based on the modified timestamp returned by `rclone`.

---

## Troubleshooting

### Lock File Exists

Message:

```text
Lock file exists, exiting.
```

Check whether another copy is running:

```bash
ps aux | grep rclone
ps aux | grep rclone_cctv_upload
```

If no copy is running, remove the stale lock:

```bash
sudo rm /var/lock/subsys/RcloneLockFile.run
```

---

### Remote Folder Does Not Exist

Message:

```text
Remote folder does not exist yet, skipping cleanup for this folder
```

This is normally OK. It means the script tried to clean a remote folder that has not been created/uploaded yet.

---

### `rclone cleanup` Not Supported

Some remotes do not support:

```bash
rclone cleanup
```

The script treats unsupported cleanup as non-fatal and continues.

You can disable cleanup per remote:

```python
empty_trash=False
```

---

### Upload Failed

Check the output from the failed upload job.

Common causes:

- wrong remote name
- expired cloud login/token
- network problem
- cloud provider rate limit
- local folder path does not exist
- not enough permissions
- not enough cloud storage space
- `sync` deleting more than expected
- `move` removing local files after transfer

Test the remote manually:

```bash
rclone lsf RemoteName:/Path
```

Test the local path:

```bash
ls -ld /path/to/local/CCTV
```

---

### Delete List Looks Wrong

The script writes temporary delete lists to:

```python
DELETE_LIST_DIR = Path("/root/rclone")
```

These files are named like:

```text
to-delete-Example-OneDrive-Encrypted__CCTV_Home_Camera01
```

You can inspect them before running a real deletion if you are testing.

---

## Exit Codes

| Exit Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Cleanup, trash cleanup, upload, or lock creation failed |
| `128 + signal` | Script was interrupted by a signal |

---

## Limitations

- No external config file yet.
- No global `--dry-run` argument yet.
- No email, MQTT, or Home Assistant notification support.
- Remote cleanup happens before upload.
- If cleanup fails, upload is skipped.
- If trash cleanup has a real failure, upload is skipped.
- Some rclone backends may not support all options.
- Trash behavior differs between cloud providers.
- `rclone cleanup` support differs between cloud providers.

---

## Disclaimer

This software is provided as-is, without warranty of any kind.

You are responsible for reading, understanding, testing, and safely configuring the script before using it.

By using this software, you agree that:

- You are responsible for your own files and backups.
- You are responsible for checking all paths and remotes.
- You are responsible for testing with safe data first.
- You are responsible for any data loss, deletion, corruption, cloud account issues, local file loss, or other damage.
- The author and contributors are not liable for any damages caused by use or misuse of this script.

Do not use this script on important data unless you have verified backups and understand exactly what it will do.

---

## License

Add your preferred license here.

Example:

```text
MIT License
```

Or keep the project private if this is only for personal use.
