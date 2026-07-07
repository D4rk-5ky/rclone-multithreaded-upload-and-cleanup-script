⚠️ Disclaimer & Responsibility
This software is provided “as is”, without any warranty of any kind.

By using this script, you accept full responsibility for:

How the code is used
What commands are executed
Testing the script in your own environment
Verifying that it behaves exactly as you expect
Any damage, data loss, downtime, or security issues caused directly or indirectly by its use
The author is not liable for:

System damage
Data loss
Accidental shutdowns or reboots
Security breaches
Misconfiguration or misuse
You must review, test, and validate the script and all configured commands before using it in production or on critical systems.

⚠️ AI-assisted / vibe-coded experimental software. Use at your own risk.

Disclaimer
This project is AI-assisted / vibe-coded software created as a hobby project. It has not been professionally audited and may contain bugs, unsafe behavior, data-loss issues, security problems, or incorrect assumptions.

You are responsible for reviewing the code, testing it in a safe environment, making backups, and understanding what it does before using it on real data. The author is not responsible for damage, data loss, broken systems, security issues, or other problems caused by using this software.

Data Loss Warning
This application can perform destructive operations on configured rclone remotes. Cleanup, age-based deletion, file-count limits, directory-size limits, and total remote-size enforcement can permanently delete remote files to make space for uploads.

If a managed remote contains backup data, incorrect remote paths, cleanup paths, filters, age settings, or size limits can delete valid backup copies and may leave you without a usable online backup. Do not treat a remote managed by this script as your only backup.

Always test with disposable data or a test remote first, run `--validate-config`, review the effective remote paths and cleanup limits, and keep at least one separate working backup that this application cannot delete.

# rclone-multithreaded-upload

Version 0.0.17

This application uploads one or more local directories to independent rclone destinations and maintains configured cleanup limits for the remote data managed by each destination.

The script is designed for unattended CCTV/archive-style workloads where old remote files may need to be deleted to make room before an upload starts.

> **Warning:** this application can delete remote files. Validate the configuration first and test against disposable data or a test remote before production use.

## Requirements

- Python 3 with support for `X | None` type syntax.
- `rclone` installed and available in `PATH`.
- All referenced rclone remotes already configured.
- Read access to each configured `local_path`.
- Permission to create the configured lock-file parent directory and delete-list directory.
- Remote permissions required by the selected rclone upload and cleanup operations.

The script itself uses only the Python standard library.

## Project files

- `rclone-multithreaded-upload.py` — executable application entry point and current orchestration/logic implementation.
- `rclone_multithreaded_upload/__init__.py` — internal Python package marker.
- `rclone_multithreaded_upload/models.py` — shared dataclasses used by configuration, cleanup, reservation, upload, and result handling.
- `config.json` — current runtime configuration for the project.
- `config.example.json` — neutral example showing every supported configuration option.
- `README.md` — current usage and configuration reference.
- `commented_code_map.md` — code/function/command map for review.
- `VERSIONING.md` — version change log.

## Basic commands

Show help:

```bash
./rclone-multithreaded-upload.py --help
```

Show the application version:

```bash
./rclone-multithreaded-upload.py --version
```

Validate a configuration without creating the lock file and without starting rclone cleanup, sizing, trash cleanup, or uploads:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Run the application:

```bash
./rclone-multithreaded-upload.py --config ./config.json
```

The config filename extension is not enforced. The supplied file must contain valid JSON.

## Command-line options

### `-c PATH`, `--config PATH`

Required for normal execution and config validation.

Loads the external JSON configuration. The script intentionally has no embedded upload remotes or local source paths.

### `--validate-config`

Loads the config, validates supported values, builds the effective cleanup targets, prints the startup summary, and exits.

It does **not**:

- acquire the lock file;
- run `rclone lsjson`;
- run `rclone size`;
- delete files;
- clean remote trash;
- upload files.

### `--version`

Prints the application version and exits.

### `-h`, `--help`

Prints argparse help and exits.

## Normal execution order

1. Load and validate JSON configuration.
2. Build full cleanup targets from every upload destination's own `cleanup_rules`.
3. Acquire the lock file.
4. Run pre-upload per-rule cleanup.
5. Run pre-upload trash cleanup where enabled.
6. Sleep for `sleep_after_step` seconds.
7. Start independent reservation/upload pipelines for each configured destination.
8. For each remote pipeline:
   1. Size the complete filtered local candidate source with `rclone size --json`.
   2. Read one recursive managed remote listing with `rclone lsjson`.
   3. Calculate whether `current managed remote bytes + reserved local bytes + 1 MiB safety headroom` would exceed `max_total_size`.
   4. When space is required, sort managed remote files oldest-first by parsed UTC `ModTime`.
   5. Select complete files until the selected bytes are at least the calculated deficit.
   6. Write one `--files-from` list and run one delete command for that reservation pass.
   7. Re-read local and remote sizes and repeat when required, up to 10 reservation cleanup passes.
   8. Optionally clean remote trash after reservation.
   9. Sleep for `sleep_after_step` seconds for that remote.
   10. Run the configured `copy`, `sync`, or `move` upload.
9. Run post-upload per-rule cleanup even when one or more uploads failed or partially transferred data.
10. Enforce post-upload `max_total_size` limits.
11. Run post-upload trash cleanup where enabled.
12. Verify final `max_files`, `max_size`, and `max_total_size` limits.
13. Print the final per-remote result summary and exit with code `0` for complete success or `1` when a required stage failed.

## Important reservation behavior

Reservation is deliberately conservative.

The application reserves space for the **complete local source selected by the source-selection filters in `copy_options`**. It does not compare the source with the destination before deciding how much space to reserve.

There is no identical-file protection in reservation cleanup. A remote file is eligible for reservation deletion when it is inside a path managed by a cleanup rule. Oldest managed files are selected by `ModTime` until enough complete-file bytes have been selected.

A one-byte transfer-cap allowance is added so an exactly measured source can reach the rclone byte cap. A fixed 1 MiB safety headroom is also required below `max_total_size` during reservation.

When `max_total_size` applies and a successful reservation stored a positive local byte count, the upload command receives:

```text
--max-transfer <reserved bytes + 1>B --cutoff-mode CAUTIOUS
```

This prevents later file growth or newly appearing source files from silently consuming unreserved quota. A source that grows beyond the reservation can therefore fail safely and be retried on a later run.

## Configuration reference

See `config.example.json` for a neutral full example containing every available option.

### Root options

#### `delete_min_age`

Type: non-empty string  
Default: `"31d"`

Used by cleanup rules with effective `delete_old_files=true`.

The value is passed to:

```text
rclone delete <target> --min-age <delete_min_age>
```

#### `lock_file`

Type: non-empty string  
Default: `/var/lock/subsys/RcloneLockFile.run`

Path to the atomic single-instance lock file.

The application creates the parent directory when necessary. If the lock already exists, the application prints `Lock file exists, exiting.` and exits with code `0`.

#### `delete_list_dir`

Type: non-empty string  
Default: `/root/rclone`

Directory used for generated `--files-from` delete lists.

Delete-list filenames are derived from sanitized remote paths.

#### `sleep_after_step`

Type: non-negative integer  
Default: `5`

Number of seconds used by the global pre-pipeline pause and by each successful reservation pipeline before its upload starts.

Use `0` to disable these waits.

### `thread_limits`

The object itself is optional. All values must be integers of at least `1` after validation.

#### `upload_threads`

Default: `2`

Maximum independent reservation/upload pipelines and therefore the maximum concurrent upload jobs.

#### `cleanup_threads`

Default: `2`

Maximum concurrent per-folder cleanup and per-folder verification jobs.

#### `remote_quota_cleanup_threads`

Default: `2`

Maximum concurrent remote-wide quota cleanup and remote-wide final verification jobs.

The legacy standalone reservation phase helper also uses this value, although normal execution uses the independent pipeline runner controlled by `upload_threads`.

#### `trash_cleanup_threads`

Default: `2`

Maximum concurrent `rclone cleanup` jobs.

### `upload_directories`

Required non-empty list.

Each object defines one local source, one rclone destination, destination defaults, destination-wide quota behavior, upload options, and cleanup rules owned only by that destination.

#### `name`

Type: non-empty string or `null`  
Default: `null`

Optional display name in startup and final result output. When omitted, `remote_path` is used.

#### `local_path`

Type: required non-empty string.

Local directory that is sized and uploaded.

The path must exist and be a directory before reservation sizing and upload.

#### `remote_path`

Type: required non-empty string.

Rclone destination root, for example:

```text
Example-Encrypted:CameraArchive
```

Cleanup-rule paths are relative to this root.

#### `upload_command`

Type: non-empty string  
Default: `"copy"`

Allowed values:

- `copy`
- `sync`
- `move`

Other rclone commands are rejected. Destructive commands such as `delete` or `purge` cannot be configured as the upload command.

`sync` receives the effective backend-specific hard-delete option when `delete_to_trash=false` and the underlying backend is explicitly supported.

#### `delete_old_files`

Type: boolean  
Default: `true`

Default inherited by cleanup rules whose own `delete_old_files` is `null` or omitted.

When effectively true, the rule first runs age cleanup using `delete_min_age`.

#### `delete_excess_files`

Type: boolean  
Default: `true`

Default inherited by cleanup rules whose own `delete_excess_files` is `null` or omitted.

At upload level this also controls `max_total_size` reservation and remote-wide quota deletion. When false, reservation cleanup and remote-wide quota cleanup are skipped.

#### `max_total_size`

Type: size string or `null`  
Default: `null`

Maximum combined size of files managed by this upload destination's cleanup-rule paths.

Examples:

```text
500M
50G
1T
1.5TB
```

Accepted units are `B`, `K`, `KB`, `M`, `MB`, `G`, `GB`, `T`, and `TB`. Units use powers of 1024 internally.

`max_total_size` is used for pre-upload reservation, post-upload quota cleanup, and final quota verification.

Only files covered by at least one cleanup rule are included in the managed remote size.

#### `delete_to_trash`

Type: boolean  
Default: `false`

Default deletion mode for the upload destination and inherited cleanup rules.

When true, normal backend/default trash behavior is used.

When false, the script resolves wrappers such as `crypt`, `alias`, `chunker`, `compress`, and `hasher` to the underlying configured backend and explicitly maps:

- Google Drive (`drive`) to `--drive-use-trash=false`;
- Mega (`mega`) to `--mega-hard-delete`;
- OneDrive (`onedrive`) to `--onedrive-hard-delete`.

For other backends the script does not invent an unverified hard-delete flag; normal rclone delete behavior is retained.

#### `empty_trash`

Type: boolean  
Default: `true`

Controls `rclone cleanup <remote_path>`.

The cleanup command is skipped when `delete_to_trash=false`, because script-managed deletions are considered direct for supported hard-delete backends.

When the backend reports that `cleanup` is unsupported, that condition is treated as a skipped cleanup rather than a failure.

#### `buffer_size`

Type: positive size string or `null`  
Default: `null`

When set, the upload command receives:

```text
--buffer-size <buffer_size>
```

The option is configured separately because the script owns this flag and rejects it inside `copy_options`.

#### `copy_options`

Type: list of strings  
Default: `[]`

Additional arguments appended to the selected rclone `copy`, `sync`, or `move` command.

Both split and equals forms are supported for options, for example:

```json
["--max-age", "12h"]
```

or:

```json
["--max-age=12h"]
```

The following source-selection options are also copied to `rclone size` so reservation sizing follows the same source file selection:

```text
--min-age
--max-age
--min-size
--max-size
--include
--include-from
--exclude
--exclude-from
--filter
--filter-from
--files-from
--files-from-raw
--ignore-case
```

Other runtime/statistics/transfer options remain upload-only.

The script rejects these flags in `copy_options` because it manages them itself or because they could invalidate reservation behavior:

```text
--absolute
--combined
--compare-dest
--copy-dest
--csv
--dest-after
--dirs-only
--dry-run
--format
--cutoff-mode
--max-duration
--max-transfer
--buffer-size
--no-traverse
--separator
-n
```

#### `cleanup_rules`

Type: list  
Default: `[]`

Each rule belongs only to its containing `upload_directories` entry.

Top-level `directory_cleanup_rules` is explicitly rejected.

Duplicate normalized rule paths within the same upload destination are rejected.

A destination with no cleanup rules has no managed files for `max_total_size` reservation/quota accounting.

### Cleanup rule options

#### `path`

Type: required non-empty string.

Path relative to the owning `remote_path`.

`"/"` means the upload remote root.

Example:

```text
remote_path = Example-Encrypted:CameraArchive
path        = FrontDoor
```

Produces the cleanup target:

```text
Example-Encrypted:CameraArchive/FrontDoor
```

#### `max_files`

Type: positive integer or `null`  
Default: `null`

Keeps at most this many newest files in the rule target when effective `delete_excess_files=true`.

Oldest files are deleted first.

#### `max_size`

Type: size string or `null`  
Default: `null`

Keeps the total recursive file size of the rule target at or below this limit when effective `delete_excess_files=true`.

Oldest files are deleted first.

When both `max_files` and `max_size` are set, complete oldest files are removed until **both** limits are satisfied.

#### `delete_old_files`

Type: boolean or `null`  
Default: `null`

`null` or omission inherits the upload-level `delete_old_files` value.

#### `delete_excess_files`

Type: boolean or `null`  
Default: `null`

`null` or omission inherits the upload-level `delete_excess_files` value.

#### `delete_to_trash`

Type: boolean or `null`  
Default: `null`

`null` or omission inherits the upload-level `delete_to_trash` value.

This changes deletion behavior for that cleanup target. Remote-wide reservation and `max_total_size` cleanup use the upload-level deletion mode.

## Cleanup ordering

All destructive file selection based on age uses recursive `rclone lsjson` data.

Every file must have a valid:

- `Path`;
- non-negative integer `Size`;
- timezone-bearing RFC3339 `ModTime`.

Malformed or incomplete metadata is a hard failure because cleanup decisions must not be made from an incomplete remote view.

`ModTime` is parsed and converted to UTC. Files sort by:

1. parsed UTC modification time, oldest first;
2. normalized relative path as the deterministic tie-breaker.

## Delete-list behavior

Generated delete lists are written below `delete_list_dir` and supplied to rclone with `--files-from`.

Per-rule list prefix:

```text
to-delete-
```

Remote quota list prefix:

```text
to-delete-remote-quota-
```

Reservation list prefix:

```text
to-delete-upload-reservation-
```

The configured remote path is converted to a safe filename by replacing `:` and `/` with `_`.

## Lock behavior

The lock file is created atomically with `O_CREAT | O_EXCL`.

The file contains the current process ID:

```text
pid=<pid>
```

The lock is removed at normal process exit through `atexit` and when SIGINT or SIGTERM is handled.

SIGINT exits with code `130`. SIGTERM exits with code `143`.

The application does not inspect whether a pre-existing lock PID is alive. A stale lock therefore remains a manual administrative condition.

## Final result and exit status

Each remote tracks four result stages:

```text
Reservation
Upload
Post cleanup
Final quota
```

Stage states are:

```text
PENDING
SUCCESS
FAILED
SKIPPED
```

The final result includes captured failure context for failed stages and a list of failed remotes.

Exit code:

- `0` — all required upload, post-cleanup, trash, and final verification phases succeeded;
- `1` — configuration, a required phase, upload pipeline, or final verification failed;
- `128 + signal number` — SIGINT/SIGTERM signal exit path.

A failed upload does **not** prevent global post-upload cleanup and final verification. This is deliberate because a failed rclone upload can have transferred partial data.
