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

Version 0.0.18

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

The application uses only the Python standard library.

## v0.0.18 modular layout

The old executable is now only a compatibility entry point. Runtime logic lives in the package modules:

```text
rclone-multithreaded-upload.py
rclone_multithreaded_upload/
├── __init__.py
├── cleanup.py
├── cli.py
├── commands.py
├── config.py
├── lock.py
├── main.py
├── models.py
├── output.py
├── phases.py
├── rclone_backend.py
├── remote_files.py
├── reservation.py
├── results.py
├── state.py
├── summary.py
├── targets.py
├── upload.py
├── utils.py
└── verification.py
```

Additional project files:

- `config.json` — current runtime configuration, unchanged from v0.0.17.
- `config.example.json` — neutral full configuration example, unchanged from v0.0.17.
- `tests/test_logic.py` — eight focused non-destructive regression tests for the moved logic.
- `tests/test_integration_fake_rclone.py` — one full-flow integration test using a temporary fake `rclone`; it never contacts real cloud remotes.
- `commented_code_map.md` — module/function/command map.
- `VERSIONING.md` — release changes.

## Why `state.py` exists

The v0.0.17 single-file implementation updated mutable module globals in `load_config()`.

Simply moving those globals into `config.py` and importing values such as:

```python
from config import UPLOAD_THREADS
```

would risk stale imported integers and `Path` objects after `load_config()` changed the original module variables.

v0.0.18 uses one shared `RuntimeState` instance:

```python
from .state import STATE
```

Modules read values such as:

```python
STATE.upload_threads
STATE.delete_list_dir
STATE.sleep_after_step
```

at runtime. The configuration loader updates the same state object used by cleanup, reservation, upload, phase, lock, result, and summary modules.

This is a module-boundary change. It is not intended to change the configured behavior.

## Basic commands

Show help:

```bash
./rclone-multithreaded-upload.py --help
```

Show version:

```bash
./rclone-multithreaded-upload.py --version
```

Validate configuration without creating the lock file or running rclone cleanup, sizing, trash cleanup, or upload commands:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Run the application:

```bash
./rclone-multithreaded-upload.py --config ./config.json
```

## Normal execution order

1. Load and validate JSON configuration.
2. Build full cleanup targets from every destination's own `cleanup_rules`.
3. Acquire the atomic single-instance lock.
4. Run pre-upload per-rule cleanup.
5. Run pre-upload trash cleanup where enabled.
6. Sleep for `sleep_after_step` seconds.
7. Start independent reservation/upload pipelines using `upload_threads`.
8. Each remote pipeline independently:
   1. sizes the filtered local source with `rclone size --json`;
   2. performs one recursive `rclone lsjson` for the managed remote root;
   3. calculates `managed remote bytes + reserved local bytes + 1 MiB safety headroom`;
   4. selects the oldest complete managed files until selected bytes are at least the exact deficit;
   5. runs one `rclone delete --files-from` for the reservation pass when required;
   6. re-reads local and remote sizes and repeats up to 10 cleanup passes;
   7. performs that remote's post-reservation trash cleanup where configured;
   8. sleeps for `sleep_after_step` seconds for that remote;
   9. starts that remote's `copy`, `sync`, or `move` upload immediately.
9. Run post-upload per-rule cleanup even if an upload failed or partially transferred data.
10. Enforce post-upload `max_total_size` limits.
11. Run post-upload trash cleanup where enabled.
12. Verify final `max_files`, `max_size`, and `max_total_size` limits.
13. Print `FINAL RUN RESULT` and exit `0` only when all required stages succeed.

## Independent remote pipeline behavior

There is deliberately **no global reservation barrier**.

For example, this is valid:

```text
GDrive   : reservation -> delete -> upload
Mega     : reservation -> lsjson still running
OneDrive : reservation -> lsjson still running
```

A fast remote is allowed to upload after its own reservation and post-reservation trash step finish. It does not wait for unrelated remotes.

The v0.0.18 regression suite verifies this ordering twice: once with a focused concurrent pipeline test and once through the real entry point using the fake-rclone integration environment.

## Reservation behavior

Reservation remains deliberately conservative.

The application reserves space for the **complete local source selected by source-selection filters in `copy_options`**. It does not perform a source-versus-destination comparison before deciding the reservation size.

Source-selection filters copied to `rclone size` include:

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

Runtime options such as `--stats`, `--transfers`, and `--buffer-size` are not forwarded to `rclone size` because they do not change the selected source byte total.

The reservation byte deficit is:

```text
current managed remote bytes
+ reserved upload cap
+ 1 MiB safety headroom
- max_total_size
```

When the value is positive, managed remote files are sorted by parsed UTC `ModTime`, oldest first. Complete files are selected until the selected byte total is at least the deficit.

There is no identical-file protection in reservation cleanup. A managed remote file is eligible based on the cleanup-rule scope and age ordering.

After successful reservation, the measured local byte count is stored for that remote. A positive reserved upload receives:

```text
--max-transfer <reserved bytes + 1>B --cutoff-mode CAUTIOUS
```

This preserves the previous fail-safe behavior when the source grows after reservation.

## Cleanup rules and `max_total_size`

Each `upload_directories` entry owns its own `cleanup_rules`.

A cleanup-rule path is relative to that entry's `remote_path`:

```json
{
  "path": "/",
  "max_size": "12G"
}
```

`"/"` means the configured `remote_path` root.

Per-rule values:

- `max_files`: positive integer or `null`.
- `max_size`: size string or `null`.
- `delete_old_files`: `true`, `false`, or `null` to inherit the upload default.
- `delete_excess_files`: `true`, `false`, or `null` to inherit the upload default.
- `delete_to_trash`: `true`, `false`, or `null` to inherit the upload default.

`max_total_size` is enforced across files managed by the upload entry's cleanup-rule paths. One recursive remote-root listing is read and Python filters the returned entries to managed rule paths. Overlapping rule coverage is deduplicated by relative file path.

## Upload commands

Supported values are intentionally restricted to:

```text
copy
sync
move
```

The following cannot be selected as `upload_command`:

```text
delete
purge
cleanup
```

For `sync`, backend-specific direct-delete flags are added only when `delete_to_trash=false` and the underlying backend is one of the explicitly mapped backends:

```text
drive    -> --drive-use-trash=false
mega     -> --mega-hard-delete
onedrive -> --onedrive-hard-delete
```

Wrapper remotes such as `crypt` are resolved through `rclone config dump`. The captured rclone configuration is retained in memory and is not printed because it may contain obscured credentials or tokens.

## Lock behavior

The lock file is created atomically with `O_CREAT | O_EXCL`.

If the configured lock already exists:

```text
Lock file exists, exiting.
```

The process exits with code `0`, matching the previous behavior.

Only a process that successfully created the lock attempts to remove it.

## Final result state

Each remote retains four stages:

```text
Reservation
Upload
Post cleanup
Final quota
```

Each stage ends as:

```text
SUCCESS
FAILED
SKIPPED
```

Failed command output is filtered for error-bearing lines. If no error-like line is found, the last non-empty command lines are kept so a non-zero command retains context in `FINAL RUN RESULT`.

## Non-destructive regression tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

The nine-test suite does not contact real rclone remotes or delete cloud data. It contains eight focused logic/concurrency tests plus one full-flow integration test that runs the real v0.0.18 entry point against a temporary fake `rclone` state store.

It verifies:

- binary size parsing;
- source-filter extraction for `rclone size`;
- one remote-root listing and managed-path deduplication;
- oldest-complete-file cleanup selection;
- exact reservation deficit and full-file over-selection;
- upload buffer/filter/transfer-cap command construction;
- independent per-remote pipeline concurrency with no global reservation barrier;
- current production config thread counts, remote names, and `12G` / `12G` / `50G` limits;
- complete entry-point phase flow through reservation deletion, upload, post-upload cleanup, final verification, and final result reporting using fake remote state.

## Updating from v0.0.17

Keep the complete directory layout together. The executable imports the internal package modules.

Replace the old v0.0.17 executable and package with the v0.0.18 files, while retaining your desired `config.json`.

The supplied v0.0.18 `config.json` and `config.example.json` are intentionally unchanged from the v0.0.17 configuration schema and values.

Before the first real run:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Then run the non-destructive tests:

```bash
python3 -m unittest discover -s tests -v
```

A live production run is still required to prove provider-specific rclone behavior against your actual GDrive, MEGA, and OneDrive remotes. The fake-rclone integration test exercises the application end to end, but it does not emulate provider APIs, throttling, trash semantics, crypt behavior, or cloud-specific rclone responses.
