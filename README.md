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

Version 0.0.19

Uploads one or more local directories to independent rclone destinations and maintains configured age, file-count, folder-size, and managed remote-size limits.

v0.0.19 replaces repeated remote re-listing with a three-snapshot planner.

## Main v0.0.19 optimization

A normal successful remote pipeline now performs exactly three recursive remote `rclone lsjson` commands:

```text
1. PRE-UPLOAD snapshot
   -> age cleanup planning
   -> max_files / max_size planning
   -> upload reservation planning
   -> combined delete plan
   -> upload

2. POST-UPLOAD snapshot
   -> age cleanup planning
   -> max_files / max_size planning
   -> max_total_size planning
   -> combined delete plan

3. FINAL snapshot
   -> verify every max_files / max_size / max_total_size limit
```

The three-snapshot target refers to recursive remote listing commands. Actual `rclone delete`, `copy` / `sync` / `move`, optional `rclone cleanup`, and backend detection still run when required. A recursive listing may also require multiple provider API pages internally.

## Why it is faster

`rclone lsjson --recursive --files-only --no-mimetype` already provides the file path, size, and modification time needed by the cleanup and reservation rules.

v0.0.19 keeps each listing in memory as a `RemoteSnapshot` and calculates deletion decisions in Python.

When a file is selected for deletion, it is immediately removed from the working snapshot. The next rule therefore sees the simulated post-delete state without another remote listing.

Example:

```text
PRE-UPLOAD SNAPSHOT
        |
        +--> age rule selects old files
        |       working snapshot removes them
        |
        +--> max_files / max_size sees remaining files
        |       working snapshot removes selected files
        |
        +--> reservation sees remaining managed files
        |       selects oldest complete files for exact deficit
        v
COMBINED DELETE PLAN
        |
        v
rclone delete --files-from ...
        |
        v
UPLOAD
```

Files are still selected oldest first and only complete files are selected.

## Combined delete plans

Age cleanup, cleanup-rule limits, and upload reservation no longer each execute their own remote listing and delete-list calculation.

They add files to one `RemoteDeletePlan`.

The same file is selected only once because a selected path is removed from the working snapshot immediately.

Normally one delete command is executed for the complete phase:

```bash
rclone delete --files-from DELETE_LIST REMOTE:
```

If one remote has cleanup rules that intentionally mix trash and hard-delete modes, the combined plan is split by delete mode. This preserves the configured delete behavior and may require two delete commands for that phase.

## Local source size cache

The filtered local source size is also cached with a concurrent single-flight calculation.

The cache key contains:

```text
normalized local_path
+
source-selection filter options
```

Source-selection options include:

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

With the supplied production configuration, GDrive, MEGA, and OneDrive use the same Frigate source and the same source-selection filters. The normal run therefore performs one `rclone size --json` scan and shares the result between the three remote pipelines.

Upload/runtime options such as `--stats`, `--transfers`, and `--buffer-size` are not part of the size cache key because they do not change which local files are selected.

## Reservation logic

Reservation remains conservative.

The local candidate size is the complete local source selected by the upload source filters. The script does not first compare local and remote files to estimate only missing uploads.

The reservation deficit is:

```text
managed remote bytes after planned pre-cleanup
+ reserved upload cap
+ 1 MiB safety headroom
- max_total_size
```

If the result is positive, the planner selects oldest complete managed remote files until the selected byte total is at least the exact deficit.

The upload keeps the existing safety cap:

```text
--max-transfer <measured local bytes + 1 byte>
--cutoff-mode CAUTIOUS
```

The previous repeated reservation loop and repeated remote/local re-reading have been removed. The pre-upload snapshot is the planning state for that remote. Final live verification still occurs after post-upload cleanup.

## Independent remote pipelines

There is no global snapshot or reservation barrier.

A fast remote may progress like this:

```text
GDrive   : pre-snapshot -> plan -> delete -> upload
MEGA     : pre-snapshot still running
OneDrive : pre-snapshot still running
```

Each remote waits only for its own pre-upload snapshot, planning, planned delete, optional trash cleanup, and configured sleep.

## Execution order

```text
load and validate config
        |
        v
acquire lock
        |
        v
independent per-remote pipelines
        |
        +--> PRE-UPLOAD lsjson snapshot       [snapshot 1]
        +--> plan age cleanup
        +--> plan max_files / max_size
        +--> get cached local source size when reservation is enabled
        +--> plan max_total_size upload reservation
        +--> execute combined pre-upload delete plan
        +--> optional trash cleanup
        +--> configured sleep
        +--> upload immediately for that remote
        |
        v
post-upload per-remote cleanup
        |
        +--> POST-UPLOAD lsjson snapshot      [snapshot 2]
        +--> plan age cleanup
        +--> plan max_files / max_size
        +--> plan max_total_size
        +--> execute combined post-upload delete plan
        +--> optional trash cleanup
        |
        v
final per-remote verification
        |
        +--> FINAL lsjson snapshot            [snapshot 3]
        +--> verify all cleanup rules
        +--> verify max_total_size
        |
        v
FINAL RUN RESULT
```

Post-upload cleanup and final verification still run when an upload fails or partially transfers data.

## Module layout

```text
rclone-multithreaded-upload.py
rclone_multithreaded_upload/
├── __init__.py
├── cleanup.py
├── cli.py
├── commands.py
├── config.py
├── delete_plan.py
├── lock.py
├── main.py
├── models.py
├── output.py
├── phases.py
├── planning.py
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

Important responsibilities:

- `remote_files.py` — recursive JSON listing and in-memory snapshot filtering.
- `planning.py` — age, rule-limit, max-total-size, and reservation planning.
- `delete_plan.py` — combined deletion selection bookkeeping and delete execution.
- `reservation.py` — filtered local-size parsing and concurrent single-flight cache.
- `phases.py` — independent three-snapshot workflow orchestration.
- `verification.py` — all final limits checked from one final snapshot per remote.

## Requirements

- Python 3 with `X | None` type syntax support.
- `rclone` available in `PATH`.
- Configured rclone remotes.
- Read access to local upload paths.
- Write permission for the lock-file parent and delete-list directory.
- Remote permissions required for the configured upload and delete operations.

The Python application uses only the standard library.

## Commands

Show version:

```bash
./rclone-multithreaded-upload.py --version
```

Validate the JSON configuration without creating the lock or executing rclone work:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Run:

```bash
./rclone-multithreaded-upload.py --config ./config.json
```

## Configuration compatibility

The v0.0.19 configuration schema is unchanged from v0.0.18.

The supplied `config.json` still contains:

```text
upload_threads                4
cleanup_threads               4
remote_quota_cleanup_threads  4
trash_cleanup_threads         4

GDrive    max_total_size 12G
Mega      max_total_size 12G
OneDrive  max_total_size 50G
```

`cleanup_threads` and `remote_quota_cleanup_threads` are both retained for configuration compatibility. Combined post-upload cleanup and final-verification worker counts use the higher configured value because cleanup-rule and remote-quota work now share the same per-remote snapshot phase.

## Age cleanup

Age cleanup is now planned from the `ModTime` values in the remote JSON snapshot rather than running a separate `rclone delete --min-age` traversal.

The configured `delete_min_age` parser follows the rclone duration behavior used by the project, including seconds, `ms`, `s`, `m`, `h`, `d`, `w`, `M`, `y`, `off`, and supported absolute date/time forms.

The fixed extended duration multipliers match rclone's parser:

```text
1d = 24 hours
1w = 7 days
1M = 30 days
1y = 365 days
```

## Delete modes

The existing backend-specific direct-delete behavior remains:

```text
Google Drive -> --drive-use-trash=false
MEGA         -> --mega-hard-delete
OneDrive     -> --onedrive-hard-delete
```

when `delete_to_trash` is false and the underlying rclone backend is detected.

When `delete_to_trash` is true, backend-default/trash behavior is used.

## Final result

The final summary still records these stages per remote:

```text
Reservation
Upload
Post cleanup
Final quota
```

A failed pre-upload snapshot, plan, delete, reservation, or trash step fails `Reservation` and prevents the upload for that remote.

A failed upload does not prevent global post-upload cleanup and final verification.

The process exits `0` only when all required stages succeed.

## Tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

v0.0.19 contains 10 non-destructive tests.

The fake-rclone integration test executes the real compatibility entry point against a temporary state file. It asserts:

```text
3 lsjson commands for fast:root
3 lsjson commands for slow:root
1 local rclone size command for the shared source/filter key
no standalone rclone delete --min-age command
combined planned deletion
upload
post-upload cleanup
final verification
successful final result
fast remote uploads before slow remote finishes its first snapshot
```

The tests do not contact GDrive, MEGA, or OneDrive.

## Live-run warning

The snapshot planner has been tested with non-destructive unit tests and a fake-rclone integration environment. It has not been destructively tested against the user's real cloud remotes as part of packaging.

A provider can change while a snapshot is being planned or after it is read. v0.0.19 intentionally accepts this reduced-live-recheck model to remove repeated remote traversals. The 1 MiB reservation headroom, upload transfer cap, post-upload snapshot, cleanup, and final live snapshot verification remain safety layers, but they do not make remote deletion risk-free.
