# Rclone CCTV Multi-Remote Upload and Cleanup Script

A Python 3 script for uploading CCTV/Shinobi recordings to multiple `rclone` remotes while also cleaning old or excessive files from remote camera folders.

This project is designed for setups where one local CCTV directory is uploaded to several cloud/encrypted remotes, such as Google Drive, Mega, OneDrive, SFTP, or any other `rclone` backend.

The script supports:

- Multiple upload destinations.
- Per-remote cleanup behavior.
- Per-folder retention limits.
- Per-upload-remote total storage quota limits.
- File-count based cleanup.
- Per-folder size-based cleanup.
- Remote/provider-wide size-based cleanup.
- Optional age-based cleanup.
- Optional direct delete or trash/default delete behavior.
- Optional remote trash cleanup.
- Selectable upload command: `copy`, `sync`, or `move`.
- Threaded per-folder cleanup.
- Threaded remote quota cleanup.
- Threaded trash cleanup.
- Threaded uploads.
- A startup summary showing exactly what the script is configured to do before it starts.

> [!WARNING]
> This script can permanently delete files from your configured `rclone` remotes.
>
> Depending on your configuration, it may run commands similar to:
>
> ```bash
> rclone delete remote:/path --drive-use-trash=false
> ```
>
> That may bypass your cloud provider trash/recycle bin and make deleted files unrecoverable.
>
> **You are fully responsible for all damage, data loss, account issues, misconfiguration, or other problems caused by using this software.**
>
> The author, contributors, and anyone sharing this script are **not responsible** for any loss or damage.
>
> Test with non-important data first. Use `--dry-run` while testing.

---

## Important Design

The script separates **camera folder retention rules** from **cloud/provider cleanup rules**.

### `RCLONE_DIRECTORIES`

These are the camera folders to clean below each upload remote.

They control only:

- Relative camera folder path
- Optional per-folder `max_files`
- Optional per-folder `max_size`

They do **not** decide whether files are deleted directly, moved to trash, whether age cleanup is enabled, or what provider-wide quota should be used.

Example:

```python
RCLONE_DIRECTORIES = [
    RcloneDirectory(
        path="Home/Camera01",
        max_files=80,
    ),
    RcloneDirectory(
        path="Home/Camera02",
        max_size="50G",
    ),
]
```

### `UPLOAD_DIRECTORIES`

These are the cloud/upload destinations.

They control:

- Local source path
- Remote upload path
- Upload command: `copy`, `sync`, or `move`
- Whether old files are deleted on that remote
- Whether excessive files are deleted on that remote
- Provider-wide total size limit via `max_total_size`
- Whether deletes are direct or use trash/default backend behavior
- Whether to run `rclone cleanup` / empty trash for that remote

Example:

```python
UPLOAD_DIRECTORIES = [
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-GoogleDrive-Encrypted:/CCTV",
        upload_command="copy",
        delete_old_files=True,
        delete_excess_files=True,
        max_total_size="500G",
        delete_to_trash=False,
        empty_trash=True,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "4",
            "--exclude", "/Home/OldCamera/**",
        ],
    ),
]
```

This means each cloud provider can have its own cleanup behavior and storage quota.

For example:

```text
Google Drive remote:
  max_total_size = 500G
  direct delete
  delete old files
  delete excessive files
  empty trash

Mega remote:
  max_total_size = 250G
  trash/default delete behavior
  delete old files
  delete excessive files
  do not empty trash

OneDrive remote:
  max_total_size = 1T
  skip age-based old-file cleanup
  still enforce per-folder and provider-wide excess cleanup
  direct delete
  do not empty trash
```

---

## Cleanup Levels

The script now supports **two different size cleanup levels**.

### 1. Per-folder cleanup

Configured in `RCLONE_DIRECTORIES`.

Example:

```python
RcloneDirectory(
    path="Home/Camera01",
    max_files=80,
)
```

or:

```python
RcloneDirectory(
    path="Home/Camera02",
    max_size="50G",
)
```

This controls only that individual camera folder.

### 2. Per-upload-remote provider quota cleanup

Configured in `UPLOAD_DIRECTORIES`.

Example:

```python
UploadDirectory(
    remote_path="Example-GoogleDrive-Encrypted:/CCTV",
    max_total_size="500G",
    ...
)
```

This controls the total size used under that remote across all configured camera folders.

For example, if this remote has:

```text
Home/Camera01 = 200G
Home/Camera02 = 200G
Garage/Camera03 = 150G
Outside/Camera04 = 100G
```

Total managed size:

```text
650G
```

If:

```python
max_total_size="500G"
```

then the script deletes the oldest files across the managed camera folders until the total managed size is below `500G`.

> [!NOTE]
> `max_total_size` only counts folders listed in `RCLONE_DIRECTORIES`.
>
> It does not count unrelated folders elsewhere in the cloud account.

---

## What the Script Does

The script runs in this order:

1. **Startup summary**
   - Prints all configured camera folder rules.
   - Prints all upload remotes.
   - Prints all generated per-folder cleanup targets.
   - Prints provider-wide quota cleanup settings.
   - Prints whether each remote will delete old files, delete excess files, use direct delete, use trash/default behavior, and empty trash.

2. **Per-folder remote cleanup**
   - Builds full cleanup paths by combining every upload remote with every camera folder.
   - Optionally deletes files older than `DELETE_MIN_AGE`.
   - Optionally lists files and creates a delete list for files above `max_files` or `max_size`.
   - Deletes oldest files first until per-folder limits are satisfied.

3. **Per-upload-remote quota cleanup**
   - Checks all managed files below each upload remote.
   - Counts only folders listed in `RCLONE_DIRECTORIES`.
   - If `max_total_size` is set, deletes oldest managed files until the remote is below that limit.

4. **Remote trash cleanup**
   - Runs `rclone cleanup` for upload remotes where `empty_trash=True`.
   - Skips remotes where `empty_trash=False`.
   - Treats unsupported `rclone cleanup` as non-fatal.

5. **Upload**
   - Runs `rclone copy`, `rclone sync`, or `rclone move` from the local CCTV folder to each configured remote.
   - Upload jobs run in parallel according to `UPLOAD_THREADS`.

The script will **not upload new files** if cleanup, remote quota cleanup, or trash cleanup has a real failure.

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

## Example Generated Cleanup Targets

If you configure:

```python
RCLONE_DIRECTORIES = [
    RcloneDirectory(
        path="Home/Camera01",
        max_files=80,
    ),
    RcloneDirectory(
        path="Home/Camera02",
        max_size="50G",
    ),
]
```

and:

```python
UPLOAD_DIRECTORIES = [
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-GoogleDrive-Encrypted:/CCTV",
        copy_options=[],
    ),
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-Mega-Encrypted:/CCTV",
        copy_options=[],
    ),
]
```

the script generates these per-folder cleanup targets:

```text
Example-GoogleDrive-Encrypted:/CCTV/Home/Camera01
Example-GoogleDrive-Encrypted:/CCTV/Home/Camera02
Example-Mega-Encrypted:/CCTV/Home/Camera01
Example-Mega-Encrypted:/CCTV/Home/Camera02
```

The remote quota cleanup then checks managed files under:

```text
Example-GoogleDrive-Encrypted:/CCTV
Example-Mega-Encrypted:/CCTV
```

but only inside the configured camera folders.

---

## Configuration

Configuration is currently done directly inside the Python script.

---

## Camera Folder Rules: `RCLONE_DIRECTORIES`

Example with four folders:

```python
RCLONE_DIRECTORIES = [
    RcloneDirectory(
        path="Home/Camera01",
        max_files=80,
    ),
    RcloneDirectory(
        path="Home/Camera02",
        max_size="50G",
    ),
    RcloneDirectory(
        path="Garage/Camera03",
        max_files=120,
    ),
    RcloneDirectory(
        path="Outside/Camera04",
        max_files=200,
        max_size="100G",
    ),
]
```

| Option | Meaning |
|---|---|
| `path` | Camera folder path relative to the upload remote root |
| `max_files` | Number of newest files to keep in that folder |
| `max_size` | Maximum size for that individual folder, such as `"50G"` or `"500M"` |

### `max_files`

```python
RcloneDirectory(
    path="Home/Camera01",
    max_files=80,
)
```

This keeps the newest 80 files in that camera folder if excess cleanup is enabled for the upload remote.

### Per-folder `max_size`

```python
RcloneDirectory(
    path="Home/Camera02",
    max_size="50G",
)
```

This deletes oldest files until that individual folder is below 50 GiB if excess cleanup is enabled for the upload remote.

### `max_files` and per-folder `max_size` together

```python
RcloneDirectory(
    path="Outside/Camera04",
    max_files=200,
    max_size="100G",
)
```

This deletes oldest files until both conditions are satisfied for that individual folder:

```text
file count <= 200
folder size <= 100G
```

---

## Upload Destinations: `UPLOAD_DIRECTORIES`

Example with three upload remotes:

```python
UPLOAD_DIRECTORIES = [
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-GoogleDrive-Encrypted:/CCTV",
        upload_command="copy",
        delete_old_files=True,
        delete_excess_files=True,
        max_total_size="500G",
        delete_to_trash=False,
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
        upload_command="copy",
        delete_old_files=True,
        delete_excess_files=True,
        max_total_size="250G",
        delete_to_trash=True,
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
        upload_command="sync",
        delete_old_files=False,
        delete_excess_files=True,
        max_total_size="1T",
        delete_to_trash=False,
        empty_trash=False,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "4",
            "--exclude", "/Home/OldCamera/**",
        ],
    ),
]
```

| Option | Meaning |
|---|---|
| `local_path` | Local source folder to upload from |
| `remote_path` | Remote destination root |
| `upload_command` | Rclone command used for upload: `copy`, `sync`, or `move` |
| `delete_old_files` | Whether to delete files older than `DELETE_MIN_AGE` on this remote |
| `delete_excess_files` | Whether to enforce per-folder limits and remote-wide quota cleanup on this remote |
| `max_total_size` | Maximum total size for all managed camera folders under this remote |
| `delete_to_trash` | Whether delete operations should use trash/default backend behavior |
| `empty_trash` | Whether to run `rclone cleanup` for this remote |
| `copy_options` | Extra options passed to the upload command |

---

## Remote-Wide Provider Quota: `max_total_size`

`max_total_size` belongs in `UPLOAD_DIRECTORIES`, because storage quota is normally tied to the cloud provider or account.

Example:

```python
UploadDirectory(
    remote_path="Example-GoogleDrive-Encrypted:/CCTV",
    max_total_size="500G",
    ...
)
```

This tells the script:

```text
For this remote destination, keep all managed camera folders below 500G total.
```

It checks the files in all configured `RCLONE_DIRECTORIES` under that `remote_path`.

### Disable remote-wide quota cleanup

```python
max_total_size=None
```

This disables provider-wide quota cleanup for that upload remote.

### Relationship to `delete_excess_files`

Remote-wide quota cleanup only runs when both are true:

```python
delete_excess_files=True
max_total_size is not None
```

If:

```python
delete_excess_files=False
```

then both per-folder excess cleanup and remote-wide quota cleanup are skipped for that upload remote.

---

## Delete Behavior

### `DELETE_MIN_AGE`

```python
DELETE_MIN_AGE = "31d"
```

This is used when:

```python
delete_old_files=True
```

The script runs a command similar to:

```bash
rclone delete remote:/path --min-age 31d
```

If `delete_to_trash=False`, it adds:

```bash
--drive-use-trash=false
```

---

## Direct Delete vs Trash/Default Delete

### Direct delete

```python
delete_to_trash=False
```

The script adds:

```bash
--drive-use-trash=false
```

This means direct/permanent delete where supported.

> [!CAUTION]
> Direct delete can bypass cloud trash/recycle bin and may be unrecoverable.

### Trash/default backend behavior

```python
delete_to_trash=True
```

The script does **not** add:

```bash
--drive-use-trash=false
```

This lets the `rclone` backend use its default delete behavior.

Depending on the backend, that may mean files go to trash/recycle bin.

---

## Empty Trash / `rclone cleanup`

### Enabled

```python
empty_trash=True
```

The script runs:

```bash
rclone cleanup remote:/path
```

### Disabled

```python
empty_trash=False
```

The script skips `rclone cleanup` for that remote.

> [!NOTE]
> Not every `rclone` backend supports `rclone cleanup`.
>
> The script treats unsupported cleanup as non-fatal and continues.

---

## Upload Commands

The script intentionally allows only:

```python
ALLOWED_UPLOAD_COMMANDS = {
    "copy",
    "sync",
    "move",
}
```

This avoids accidentally using destructive commands such as `delete` or `purge` as an upload command.

### `copy`

```python
upload_command="copy"
```

Uploads new/changed files and normally does not delete extra files from the destination.

This is usually the safest option for CCTV backup.

### `sync`

```python
upload_command="sync"
```

Makes the destination match the source.

> [!WARNING]
> `rclone sync` can delete files from the destination if they are not present locally.

### `move`

```python
upload_command="move"
```

Uploads files and removes the local source files after successful transfer.

> [!WARNING]
> `rclone move` can delete local CCTV recordings after upload.
>
> Use this only if you really want local files removed.

---

## Thread Limits

```python
UPLOAD_THREADS = 2
CLEANUP_THREADS = 2
REMOTE_QUOTA_CLEANUP_THREADS = 2
TRASH_CLEANUP_THREADS = 2
```

| Option | Meaning |
|---|---|
| `UPLOAD_THREADS` | Number of parallel upload jobs |
| `CLEANUP_THREADS` | Number of parallel generated per-folder cleanup target jobs |
| `REMOTE_QUOTA_CLEANUP_THREADS` | Number of parallel remote-wide quota cleanup jobs |
| `TRASH_CLEANUP_THREADS` | Number of parallel trash-cleanup jobs |

Lower these values if your machine, disk, network, or cloud providers become overloaded.

---

## Startup Summary

Before running cleanup or upload, the script prints a summary showing:

- Thread limits
- Global cleanup settings
- Camera folder rules
- Upload destinations
- Upload commands
- Per-remote cleanup behavior
- Generated per-folder cleanup targets
- Remote-wide quota cleanup settings
- Delete mode
- Empty trash setting
- Total number of targets

This helps verify the script before it starts deleting or uploading files.

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
rclone lsf Example-GoogleDrive-Encrypted:/CCTV
```

Test upload to a temporary folder:

```bash
rclone copy /path/to/test/files Example-GoogleDrive-Encrypted:/CCTV-Test --dry-run
```

Test delete behavior on a temporary folder only:

```bash
rclone delete Example-GoogleDrive-Encrypted:/CCTV-Test --min-age 31d --dry-run
```

Test direct delete behavior on temporary data only:

```bash
rclone delete Example-GoogleDrive-Encrypted:/CCTV-Test --min-age 31d --drive-use-trash=false --dry-run
```

> [!IMPORTANT]
> The script does not currently have a global `--dry-run` argument.
>
> To test safely, temporarily add `--dry-run` to the relevant `copy_options`, or add it to delete commands in the script while testing.

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

Disable empty trash for that remote if desired:

```python
empty_trash=False
```

---

### Upload Failed

Check the output from the failed upload job.

Common causes:

- Wrong remote name
- Expired cloud login/token
- Network problem
- Cloud provider rate limit
- Local folder path does not exist
- Not enough permissions
- Not enough storage space

Test manually:

```bash
rclone lsf Example-GoogleDrive-Encrypted:/CCTV
```

---

## Important Safety Notes

### This Script Deletes Remote Files

The script performs remote cleanup before upload.

Carefully check:

```python
RCLONE_DIRECTORIES
UPLOAD_DIRECTORIES
DELETE_MIN_AGE
delete_to_trash
empty_trash
upload_command
max_total_size
```

### Path Mistakes Can Be Dangerous

This is specific:

```bash
Example-OneDrive-Encrypted:/CCTV/Home/Camera01
```

This is broad and dangerous:

```bash
Example-OneDrive-Encrypted:/
```

A wrong path can cause unwanted cleanup or deletion.

### `sync` Can Delete Destination Files

If you use:

```python
upload_command="sync"
```

then destination files not present in the local source may be deleted.

### `move` Can Delete Local Files

If you use:

```python
upload_command="move"
```

then local files may be removed after successful upload.

### Direct Delete Can Be Permanent

If you use:

```python
delete_to_trash=False
```

then delete commands add:

```bash
--drive-use-trash=false
```

This may make deleted files unrecoverable.

### Remote Quota Cleanup Deletes Across Managed Folders

If you use:

```python
max_total_size="500G"
```

the script may delete the oldest files across all configured camera folders under that remote, not only from one folder.

---

## Exit Codes

| Exit Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Per-folder cleanup, remote quota cleanup, trash cleanup, upload, or lock creation failed |
| `128 + signal` | Script was interrupted by a signal |

---

## Limitations

- No external config file yet.
- No global `--dry-run` argument yet.
- No email, MQTT, or Home Assistant notification support.
- Remote cleanup happens before upload.
- If cleanup fails, upload is skipped.
- Some `rclone` backends may not support all options.
- Some `rclone` backends may treat trash/delete behavior differently.
- `max_total_size` only counts the managed folders listed in `RCLONE_DIRECTORIES`.

---

## Disclaimer

This software is provided as-is, without warranty of any kind.

You are responsible for reading, understanding, testing, and safely configuring the script before using it.

By using this software, you agree that:

- You are responsible for your own files and backups.
- You are responsible for checking all paths and remotes.
- You are responsible for testing with safe data first.
- You are responsible for any data loss, deletion, corruption, cloud account issues, or other damage.
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
