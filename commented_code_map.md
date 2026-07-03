# Commented Code Map

This file explains what each important dataclass, function, command path, and phase does, and why it exists.

## Dataclasses

### `DirectoryCleanupRule`

Represents one managed camera folder below every upload remote.

Why it exists: it keeps folder retention rules separate from cloud-specific delete behavior. A folder rule only knows the relative folder path plus optional `max_files` and `max_size` limits.

### `UploadDirectory`

Represents one local source folder and one rclone remote destination.

Why it exists: each remote can have different upload and cleanup behavior, such as `copy`, `sync`, `move`, direct delete, trash/default delete behavior, remote-wide size limits, and trash cleanup.

### `CleanupTarget`

Represents a generated full remote cleanup path.

Why it exists: the script combines every `UploadDirectory.remote_path` with every `DirectoryCleanupRule.path`. The resulting object carries folder limits from `DirectoryCleanupRule` and delete behavior from `UploadDirectory`.

### `RemoteFile`

Represents one file returned by `rclone lsf` for per-folder cleanup.

Why it exists: cleanup decisions need path, size, and modified time so oldest files can be deleted first.

### `RemoteQuotaFile`

Represents one file returned during remote-wide quota cleanup.

Why it exists: quota cleanup deletes across all managed folders under one upload remote. The path is stored relative to the upload remote so it can be used safely with `rclone delete --files-from LIST upload.remote_path`.

## Global Runtime Settings

### `VERSION`

Current project version string.

Why it exists: the CLI can report the package version with `--version`, and versioning is tracked in `VERSIONING.md`.

### `DIRECTORY_CLEANUP_RULES` and `UPLOAD_DIRECTORIES`

Loaded from the external JSON config file.

Why they exist: existing script logic is preserved by keeping the same internal setting names, while the editable values are no longer hard-coded in the Python file.

### `CONFIG_PATH`

Stores the loaded config path for the startup summary.

Why it exists: logs should clearly show which config file was used.

### `DELETE_MIN_AGE`

Age threshold used for optional old-file cleanup.

Why it exists: upload remotes can enable or disable old-file cleanup individually, while the age threshold is global.

### `ALLOWED_UPLOAD_COMMANDS`

Hard-coded allow-list containing `copy`, `sync`, and `move`.

Why it exists: this is a safety boundary. It prevents dangerous rclone commands such as `delete` or `purge` from being used as an upload command through config.

### Thread limit globals

- `UPLOAD_THREADS`
- `CLEANUP_THREADS`
- `REMOTE_QUOTA_CLEANUP_THREADS`
- `TRASH_CLEANUP_THREADS`

Why they exist: each phase can use a different concurrency limit, so slower remotes or weaker machines can be protected from too much parallel work.

### `OUTPUT_LOCK` and `OUTPUT_SEPARATOR`

Used when printing from threaded jobs.

Why they exist: without a print lock, output from parallel cleanup/upload jobs can interleave and become unreadable.

### `LOCK_FILE`

Path used to prevent multiple script instances from running at the same time.

Why it exists: running two cleanup/upload jobs at once could create duplicate work or unexpected remote delete/upload behavior.

### `DELETE_LIST_DIR`

Directory used for generated `--files-from` delete lists.

Why it exists: rclone can delete a precise list of files. The script writes those lists before calling `rclone delete --files-from`.

### `SLEEP_AFTER_STEP`

Delay between cleanup and upload.

Why it exists: preserves the original behavior of pausing after pre-cleanup before upload starts.

### `lock_created`

Tracks whether this process successfully created the lock file.

Why it exists: cleanup on exit should only remove a lock file this process owns.

## General Helper Functions

### `print_step(message)`

Prints a simple major-step message.

Why it exists: keeps logs readable in terminal, cron, and systemd output.

### `print_error(message)`

Prints an error message with consistent formatting.

Why it exists: errors should stand out clearly in logs.

### `print_job_block(job_type, job_number, target, message)`

Prints one complete block of threaded job output under `OUTPUT_LOCK`.

Why it exists: threaded jobs should not mix their output lines.

### `run_command(command, capture_output=False)`

Runs a command with `subprocess.run()` and `shell=False`.

Why it exists: commands are passed as argument lists instead of shell strings, reducing shell-escaping risk.

### `parse_size_to_bytes(size_text)`

Converts size strings such as `500M`, `50G`, and `1T` to bytes.

Why it exists: per-folder `max_size` and remote-wide `max_total_size` need numeric comparisons.

### `validate_upload_command(command)`

Normalizes and validates the configured upload command.

Why it exists: only `copy`, `sync`, and `move` are allowed as upload operations.

### `is_directory_not_found(result)`

Detects common rclone missing-directory errors.

Why it exists: missing remote camera folders are normal before the first upload and should not fail the whole run.

### `remote_name_from_path(remote_path)`

Converts a remote path into a safe local filename fragment.

Why it exists: delete-list files are named after their target remote path.

### `join_rclone_remote_path(remote_root, relative_path)`

Joins a remote root and relative folder path.

Why it exists: rclone paths need careful joining so cleanup targets are generated consistently.

### `join_relative_path(base, child)`

Joins relative paths for `--files-from` delete lists.

Why it exists: remote-wide quota delete lists must use paths relative to the upload remote root.

### `get_delete_mode_options(target)`

Returns delete options for direct-delete or trash/default delete mode.

Why it exists: `delete_to_trash=false` adds `--drive-use-trash=false`; `delete_to_trash=true` leaves backend default behavior alone.

### `get_delete_mode_text(target)`

Returns a human-readable delete mode string.

Why it exists: startup summaries should show whether deletes are direct/permanent or backend default/trash behavior.

## Config Loading Functions

### `parse_cli_args()`

Parses `--config/-c`, `--validate-config`, and `--version`.

Why it exists: the script now requires an explicit external config file and provides a safe validation-only mode.

### `load_json_config(config_path)`

Loads a JSON config file from any filename extension.

Why it exists: the user can use a custom filename like `/dest/to/conf.somextension`, but the file content must still be JSON.

### `require_string(section_name, data, key)`

Reads a required non-empty string.

Why it exists: important fields such as `local_path`, `remote_path`, and folder `path` must not be missing or blank.

### `optional_string(section_name, data, key, default)`

Reads an optional string or `null`.

Why it exists: settings such as `max_size` and `max_total_size` can be disabled with `null`.

### `optional_bool(section_name, data, key, default)`

Reads an optional boolean.

Why it exists: booleans must be real JSON `true`/`false`, not strings such as `"yes"`.

### `optional_non_negative_int(section_name, data, key, default)`

Reads an optional integer that can be zero or greater.

Why it exists: settings such as `sleep_after_step` can be zero, while negative values are invalid.

### `optional_positive_int_or_none(section_name, data, key)`

Reads a positive integer or `null`.

Why it exists: `max_files` must be positive when enabled, and `null` when disabled.

### `optional_string_list(section_name, data, key)`

Reads a list of strings.

Why it exists: rclone upload options must be passed as a safe argument list, not as a shell string.

### `parse_directory_cleanup_rules(config)`

Converts `directory_cleanup_rules` config objects into `DirectoryCleanupRule` objects.

Why it exists: it validates the user config before any cleanup or upload can start.

### `parse_upload_directories(config)`

Converts `upload_directories` config objects into `UploadDirectory` objects.

Why it exists: it validates remotes, paths, upload commands, delete behavior, quota settings, and upload options before any rclone command runs.

### `load_config(config_path_text)`

Loads the config file and applies values to the existing internal globals.

Why it exists: it preserves the existing script architecture and execution flow while moving settings out to the config file.

## Lock Handling Functions

### `acquire_lock()`

Creates the lock file atomically with `os.O_CREAT | os.O_EXCL`.

Why it exists: atomic creation avoids race conditions between two script instances starting at the same time.

### `release_lock()`

Removes the lock file if this process created it.

Why it exists: normal exits should clean up the lock file automatically.

### `signal_handler(signum, frame)`

Handles `SIGINT` and `SIGTERM`.

Why it exists: interrupted runs should release the lock file and exit with `128 + signal`.

### `sleep_after_step()`

Sleeps for the configured number of seconds.

Why it exists: keeps the original pause between pre-cleanup and upload.

## Target Building and Summary

### `build_cleanup_directories()`

Generates all per-folder cleanup targets by combining upload remotes with managed folder rules.

Why it exists: one folder rule can be applied to many remotes, while each remote still controls its own delete behavior.

### `print_startup_summary(cleanup_directories)`

Prints version, config path, thread limits, cleanup rules, upload destinations, generated cleanup targets, and quota targets.

Why it exists: the user can review the exact configured behavior before cleanup/upload starts. It is also used by `--validate-config`.

## Remote File Listing and Delete List Functions

### `get_remote_file_entries(remote_path)`

Runs:

```bash
rclone lsf --files-only --format tsp --separator '\t' remote:/path
```

Why it exists: it gets modified time, size, and path for each remote file, which is required for oldest-first cleanup.

### `make_delete_list(target)`

Creates a `--files-from` delete list for one cleanup target.

Why it exists: per-folder cleanup should delete only the oldest files necessary to satisfy `max_files`, `max_size`, or both.

### `get_upload_remote_quota_entries(upload)`

Lists files across all managed folders below one upload remote.

Why it exists: remote-wide quota cleanup should count only folders explicitly managed by the config.

### `make_upload_remote_quota_delete_list(upload)`

Creates a `--files-from` list for remote-wide quota cleanup.

Why it exists: if a remote exceeds `max_total_size`, the script deletes oldest managed files until the remote is below the configured quota.

## Cleanup Job Functions

### `cleanup_one_directory(job_number, target)`

Runs cleanup for one generated remote camera folder.

Why it exists: this function is the per-folder cleanup worker used by `ThreadPoolExecutor`.

Commands it may run:

```bash
rclone delete remote:/path --min-age AGE [delete mode options]
rclone delete --files-from LIST remote:/path [delete mode options]
```

### `cleanup_one_upload_remote_quota(job_number, upload)`

Runs remote-wide quota cleanup for one upload remote.

Why it exists: each cloud remote can have its own total managed size limit.

Command it may run:

```bash
rclone delete --files-from LIST remote:/root [delete mode options]
```

### `cleanup_one_trash_remote(job_number, upload)`

Runs trash cleanup for one upload remote when `empty_trash=true`.

Why it exists: some backends keep deleted files in trash unless `rclone cleanup` is called.

Command it may run:

```bash
rclone cleanup remote:/path
```

Unsupported cleanup is treated as non-fatal.

## Upload Job Functions

### `print_thread_output(thread_number, remote_path, line)`

Prints one line of upload output under `OUTPUT_LOCK`.

Why it exists: live upload logs should remain readable when several uploads run in parallel.

### `run_command_streamed(command, thread_number, remote_path)`

Runs an upload command with `subprocess.Popen()` and streams output live.

Why it exists: upload progress should be visible in terminal, cron logs, and systemd logs.

### `upload_one_directory(job_number, upload)`

Validates the local source folder and runs the configured rclone upload command.

Why it exists: each upload destination is handled independently and can run in parallel.

Command it may run:

```bash
rclone copy LOCAL REMOTE [copy_options]
rclone sync LOCAL REMOTE [copy_options]
rclone move LOCAL REMOTE [copy_options]
```

## Main Flow

### `main()`

Coordinates the whole program:

1. Parse CLI arguments.
2. Load and validate config.
3. Build cleanup targets.
4. For real runs, register cleanup handlers and acquire the lock.
5. Print startup summary.
6. Exit early for `--validate-config`.
7. Run per-folder cleanup.
8. Run remote-wide quota cleanup.
9. Run trash cleanup.
10. Sleep for `sleep_after_step` seconds.
11. Run uploads.
12. Exit with success or failure.

Why it exists: this preserves the original cleanup-before-upload safety behavior while adding external config loading.

## Safety Boundaries Preserved

- Commands are executed with argument lists and `shell=False`.
- Upload commands are restricted to `copy`, `sync`, and `move`.
- Cleanup happens before upload.
- Upload is skipped if cleanup fails.
- Missing remote folders are non-fatal.
- Unsupported `rclone cleanup` is non-fatal.
- A lock file prevents concurrent real runs.
- `--validate-config` does not create a lock file and does not run rclone.

## Repository Hygiene

### `.gitignore`

Defines safe ignore patterns for private/local config files, runtime logs, Python caches, build/test caches, and temporary editor files.

Why it exists: real config files can contain private rclone remote names, local paths, cloud behavior, or credentials-adjacent information. Runtime logs and generated caches should not be committed. The example config is explicitly kept trackable so users still have a complete template for all available options.

Important tracked exception:

```text
!rclone-cctv-config.example.json
```

Why it exists: the project uses JSON config content, and the example file should remain part of the repository even when local/private config files are ignored.
