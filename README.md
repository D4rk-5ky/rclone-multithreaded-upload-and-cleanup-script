# Rclone CCTV Multi-Remote Upload and Cleanup Script

Python 3 script for uploading CCTV/Shinobi recordings to multiple `rclone` remotes while also cleaning old or excessive files from managed remote camera folders.

Runtime settings are loaded from an external JSON config file with `--config` or `-c`.

```bash
python3 rclone-multithreaded-upload.py --config /path/to/rclone-cctv-config.json
```

The filename extension is not enforced. The content must be valid JSON, so a file such as `/dest/to/conf.somextension` works as long as it contains JSON.

The project includes a `.gitignore` that ignores common private config files, logs, Python cache files, build output, and temporary files while keeping the example config tracked.

> [!WARNING]
> This script can permanently delete files from configured `rclone` remotes.
>
> Depending on the config, it may run commands similar to:
>
> ```bash
> rclone delete remote:/path --drive-use-trash=false
> ```
>
> That may bypass cloud trash/recycle bin and make deleted files unrecoverable.
>
> Test with non-important data first. Review every path and remote before using real CCTV data.
> ⚠️ AI-assisted / vibe-coded experimental software. Use at your own risk.

## What the Script Does

The script runs in this order:

1. **Load config**
   - Reads the JSON config passed with `--config` or `-c`.
   - Validates upload commands, required paths, thread limits, and size strings.
   - Refuses to run without an explicit config file.

2. **Create lock file**
   - Creates the configured lock file atomically.
   - Prevents multiple copies of the script from running at the same time.

3. **Startup summary**
   - Prints version, config path, thread limits, cleanup rules, upload remotes, delete modes, generated cleanup targets, and remote-wide quota settings.

4. **Per-folder cleanup**
   - Builds full cleanup paths from every upload remote plus every configured `directory_cleanup_rules` path.
   - Optionally deletes files older than `delete_min_age` for each remote where `delete_old_files=true`.
   - Optionally deletes oldest files until each folder satisfies `max_files`, `max_size`, or both for each remote where `delete_excess_files=true`.

5. **Per-upload-remote quota cleanup**
   - Counts files only inside folders listed in `directory_cleanup_rules` below each upload remote.
   - If `max_total_size` is set, deletes oldest managed files until the total managed size is below the configured limit.

6. **Remote trash cleanup**
   - Runs `rclone cleanup` for upload remotes where `empty_trash=true`.
   - Treats unsupported `rclone cleanup` as non-fatal.

7. **Upload**
   - Runs `rclone copy`, `rclone sync`, or `rclone move` from the local CCTV folder to each configured remote.
   - Upload jobs run in parallel according to `thread_limits.upload_threads`.

The script will not upload new files if cleanup, remote quota cleanup, or trash cleanup has a real failure.

## Files

| File | Purpose |
|---|---|
| `rclone-multithreaded-upload.py` | Main script |
| `rclone-cctv-config.example.json` | Complete external config example with all available config options |
| `.gitignore` | Git ignore rules for private configs, logs, Python caches, build output, and temporary files |
| `README.md` | Current behavior and usage |
| `commented_code_map.md` | Function/command map explaining what the code does and why |
| `VERSIONING.md` | Version notes |

## Requirements

- Linux system
- Python 3
- `rclone`
- Configured `rclone` remotes
- Working access to the local CCTV/Shinobi recording folder

Check `rclone`:

```bash
rclone version
rclone listremotes
```

## Running

Copy and edit the config example:

```bash
cp rclone-cctv-config.example.json rclone-cctv-config.json
nano rclone-cctv-config.json
```

Validate the config without creating the lock file and without running any `rclone` commands:

```bash
python3 rclone-multithreaded-upload.py --config ./rclone-cctv-config.json --validate-config
```

Run the real upload/cleanup process:

```bash
python3 rclone-multithreaded-upload.py --config ./rclone-cctv-config.json
```

Short option:

```bash
python3 rclone-multithreaded-upload.py -c ./rclone-cctv-config.json
```

Show help:

```bash
python3 rclone-multithreaded-upload.py --help
```

Show version:

```bash
python3 rclone-multithreaded-upload.py --version
```

## Config Format

The config file is JSON. The extension is not checked, but the content must be valid JSON.

A complete example is included in:

```text
rclone-cctv-config.example.json
```

### Complete Config Example

```json
{
  "delete_min_age": "31d",
  "lock_file": "/var/lock/subsys/RcloneLockFile.run",
  "delete_list_dir": "/root/rclone",
  "sleep_after_step": 5,
  "thread_limits": {
    "upload_threads": 2,
    "cleanup_threads": 2,
    "remote_quota_cleanup_threads": 2,
    "trash_cleanup_threads": 2
  },
  "directory_cleanup_rules": [
    {
      "path": "Home/Camera01",
      "max_files": 80,
      "max_size": null
    },
    {
      "path": "Home/Camera02",
      "max_files": null,
      "max_size": "50G"
    },
    {
      "path": "Outside/Camera04",
      "max_files": 200,
      "max_size": "100G"
    }
  ],
  "upload_directories": [
    {
      "local_path": "/path/to/local/CCTV",
      "remote_path": "Example-GoogleDrive-Encrypted:/CCTV",
      "upload_command": "copy",
      "delete_old_files": true,
      "delete_excess_files": true,
      "max_total_size": "500G",
      "delete_to_trash": false,
      "empty_trash": true,
      "copy_options": [
        "--max-age",
        "3h",
        "--stats",
        "10s",
        "--stats-one-line",
        "--transfers",
        "4",
        "--exclude",
        "/Home/OldCamera/**"
      ]
    }
  ]
}
```

## Config Options

### Root Options

| Option | Required | Meaning |
|---|---:|---|
| `delete_min_age` | No | Age used for old-file cleanup when an upload remote has `delete_old_files=true`, for example `31d` |
| `lock_file` | No | Lock file path used to prevent multiple running instances |
| `delete_list_dir` | No | Directory where generated `--files-from` delete lists are written |
| `sleep_after_step` | No | Seconds to sleep between cleanup and upload |
| `thread_limits` | No | Object containing the four thread limits |
| `directory_cleanup_rules` | Yes | List of managed remote folders and per-folder limits |
| `upload_directories` | Yes | List of local-to-remote upload jobs and per-remote cleanup behavior |

### `thread_limits`

| Option | Meaning |
|---|---|
| `upload_threads` | Number of parallel upload jobs |
| `cleanup_threads` | Number of parallel per-folder cleanup jobs |
| `remote_quota_cleanup_threads` | Number of parallel remote-wide quota cleanup jobs |
| `trash_cleanup_threads` | Number of parallel `rclone cleanup` jobs |

All thread limits must be at least `1`.

### `directory_cleanup_rules`

Each rule defines one managed folder below each upload remote.

| Option | Meaning |
|---|---|
| `path` | Folder path relative to the upload remote root |
| `max_files` | Number of newest files to keep in this folder, or `null` |
| `max_size` | Maximum total size to keep in this folder, such as `50G`, or `null` |

`max_files` and `max_size` can be used together. The script deletes oldest files until both limits are satisfied.

### `upload_directories`

Each upload object defines one local source and one remote destination.

| Option | Meaning |
|---|---|
| `local_path` | Local source folder to upload from |
| `remote_path` | Remote destination root |
| `upload_command` | Allowed values: `copy`, `sync`, `move` |
| `delete_old_files` | Whether to delete files older than `delete_min_age` on this remote |
| `delete_excess_files` | Whether to enforce per-folder limits and remote-wide quota cleanup on this remote |
| `max_total_size` | Maximum total size for all managed folders under this remote, or `null` |
| `delete_to_trash` | `false` adds `--drive-use-trash=false`; `true` uses backend default/trash behavior |
| `empty_trash` | Whether to run `rclone cleanup` for this remote |
| `copy_options` | Extra options passed to the upload command |

## Upload Commands

Allowed upload commands are intentionally restricted:

```text
copy
sync
move
```

This prevents accidentally using destructive commands such as `delete` or `purge` as an upload command.

### `copy`

```json
"upload_command": "copy"
```

Uploads new/changed files and normally does not delete extra files from the destination. This is usually the safest option for CCTV backup.

### `sync`

```json
"upload_command": "sync"
```

Makes the destination match the source.

> [!WARNING]
> `rclone sync` can delete destination files if they are not present locally.

### `move`

```json
"upload_command": "move"
```

Uploads files and removes local source files after successful transfer.

> [!WARNING]
> `rclone move` can delete local CCTV recordings after upload.

## Delete Behavior

### Old-file cleanup

When an upload remote has:

```json
"delete_old_files": true
```

The script runs a command similar to:

```bash
rclone delete remote:/path --min-age 31d
```

The age comes from root config option `delete_min_age`.

### Direct delete

When an upload remote has:

```json
"delete_to_trash": false
```

Delete commands add:

```bash
--drive-use-trash=false
```

This can mean direct/permanent deletion where supported.

### Trash/default backend behavior

When an upload remote has:

```json
"delete_to_trash": true
```

The script does not add `--drive-use-trash=false`, so the backend default behavior is used.

### Empty trash

When an upload remote has:

```json
"empty_trash": true
```

The script runs:

```bash
rclone cleanup remote:/path
```

Unsupported `rclone cleanup` is treated as non-fatal.

## Remote-Wide `max_total_size`

`max_total_size` belongs in each `upload_directories` entry.

Example:

```json
"max_total_size": "500G"
```

The script counts only files inside folders listed in `directory_cleanup_rules` under that upload remote. It does not count unrelated files elsewhere in the cloud account or remote.

Disable remote-wide quota cleanup for a remote with:

```json
"max_total_size": null
```

## Startup Summary

Before cleanup and upload, the script prints:

- Version and config file path
- Thread limits
- Global cleanup settings
- Directory cleanup rules
- Upload destinations
- Generated per-folder cleanup targets
- Remote-wide quota cleanup settings
- Delete mode
- Empty trash setting
- Total number of targets

Use `--validate-config` to print this summary safely without starting cleanup or upload.

## Lock File

The lock file path is configured with:

```json
"lock_file": "/var/lock/subsys/RcloneLockFile.run"
```

If the script exits normally, the lock file is removed automatically.

If the system crashes or the script is killed forcefully, you may need to remove a stale lock manually after verifying the script is not still running:

```bash
ps aux | grep rclone
ps aux | grep rclone-multithreaded-upload
sudo rm /var/lock/subsys/RcloneLockFile.run
```

## Example Cron Job

```cron
0 * * * * /usr/bin/python3 /opt/rclone-cctv/rclone-multithreaded-upload.py --config /opt/rclone-cctv/rclone-cctv-config.json >> /var/log/rclone-cctv.log 2>&1
```

## Example systemd Service

```ini
[Unit]
Description=Rclone CCTV Upload and Cleanup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/rclone-cctv/rclone-multithreaded-upload.py --config /opt/rclone-cctv/rclone-cctv-config.json
```

Example timer:

```ini
[Unit]
Description=Run Rclone CCTV Upload and Cleanup every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rclone-cctv.timer
```

## Troubleshooting

### Config Validation Only

```bash
python3 rclone-multithreaded-upload.py -c ./rclone-cctv-config.json --validate-config
```

This loads the config and prints the generated summary, but does not create the lock file and does not run `rclone`.

### Lock File Exists

Message:

```text
Lock file exists, exiting.
```

Check whether another copy is running:

```bash
ps aux | grep rclone
ps aux | grep rclone-multithreaded-upload
```

If no copy is running, remove the stale lock:

```bash
sudo rm /var/lock/subsys/RcloneLockFile.run
```

### Remote Folder Does Not Exist

Message:

```text
Remote folder does not exist yet, skipping cleanup for this folder
```

This is normally OK. It means the script tried to clean a remote folder that has not been created/uploaded yet.

### Upload Failed

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

## Important Safety Notes

Carefully check these config values before running on real data:

```text
directory_cleanup_rules
upload_directories
delete_min_age
delete_to_trash
empty_trash
upload_command
max_total_size
```

Broad remote paths can be dangerous.

Specific:

```text
Example-OneDrive-Encrypted:/CCTV/Home/Camera01
```

Dangerously broad:

```text
Example-OneDrive-Encrypted:/
```

## Exit Codes

| Exit Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Config, cleanup, remote quota cleanup, trash cleanup, upload, or lock creation failed |
| `128 + signal` | Script was interrupted by a signal |

## Limitations

- Config format is JSON only, although the filename extension is not enforced.
- No global `--dry-run` argument for delete operations yet.
- Upload dry-run can be tested by adding `--dry-run` to `copy_options`.
- No email, MQTT, or Home Assistant notification support.
- Remote cleanup happens before upload.
- If cleanup fails, upload is skipped.
- Some `rclone` backends may not support all options.
- Some `rclone` backends may treat trash/delete behavior differently.
- `max_total_size` only counts folders listed in `directory_cleanup_rules`.

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

## License

Add your preferred license here.

Example:

```text
MIT License
```

Or keep the project private if this is only for personal use.
