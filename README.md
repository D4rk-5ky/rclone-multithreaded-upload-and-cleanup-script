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

Version 0.0.20

`rclone-multithreaded-upload` uploads one or more local directories to independent rclone destinations while enforcing configured age, file-count, folder-size, and managed remote-size limits.

The application is deliberately stage-based. Threads inside a stage run independently, but the next stage does not begin until every worker in the current stage has finished.

## Execution order

```text
1. PRE-UPLOAD PREPARATION
   For every remote in parallel:
   - recursive remote snapshot
   - age cleanup planning
   - cleanup_rules max_files/max_size planning
   - filtered local source sizing when max_total_size reservation is enabled
   - max_total_size upload-reservation planning
   - execute the combined planned delete list

   BARRIER: wait for every preparation worker.

2. PRE-UPLOAD TRASH CLEANUP
   For every remote whose preparation succeeded:
   - optional rclone cleanup
   - configured sleep_after_step

   BARRIER: wait for every eligible trash-cleanup worker.

3. UPLOAD
   For every remote whose preparation and pre-upload trash cleanup succeeded:
   - rclone copy, sync, or move

   BARRIER: wait for every upload worker.

4. POST-UPLOAD CLEANUP
   For every remote in parallel, including remotes whose upload failed:
   - recursive remote snapshot
   - age cleanup planning
   - cleanup_rules max_files/max_size planning
   - max_total_size cleanup planning
   - execute the combined planned delete list

   BARRIER: wait for every post-upload cleanup worker.

5. POST-UPLOAD TRASH CLEANUP
   For every remote whose post-upload cleanup stage succeeded:
   - optional rclone cleanup

   BARRIER: wait for every eligible trash-cleanup worker.

6. FINAL VERIFICATION
   For every remote in parallel:
   - recursive remote snapshot
   - verify cleanup_rules max_files/max_size limits
   - verify max_total_size

7. FINAL RUN RESULT
   - print per-remote stage status and captured errors
   - return exit code 0 only when every required stage succeeded
```

A normal successful remote therefore uses three recursive `rclone lsjson` snapshots: PRE-UPLOAD, POST-UPLOAD, and FINAL.

## Failure isolation

One failed worker does not cancel the other workers in the same stage.

- If one remote fails PRE-UPLOAD preparation, the other remote preparation jobs continue. That failed remote does not run pre-upload trash cleanup or upload, but the remotes that passed the prerequisite stage continue.
- If one remote fails pre-upload trash cleanup, the other trash-cleanup jobs continue. That failed remote does not upload, but other eligible remotes still upload.
- If one upload fails, the other upload threads continue.
- Post-upload cleanup and final verification still run for all configured remotes after upload failures because an interrupted upload may already have transferred partial data.
- If a post-upload planned delete fails for one remote, that remote does not immediately empty trash afterward; other remotes continue and final verification still runs.
- Any recorded stage failure makes the final process exit code `1`.

This keeps prerequisite safety behavior intact while preventing one failed remote from stopping unrelated remotes.

## Combined cleanup planning

Each PRE-UPLOAD or POST-UPLOAD recursive snapshot is copied into an in-memory working snapshot.

When a file is selected for deletion, the path is immediately removed from the working snapshot. Later cleanup rules therefore see the simulated post-delete state without another remote listing.

Files can be selected for:

- `delete_min_age`
- `cleanup_rules[].max_files`
- `cleanup_rules[].max_size`
- pre-upload `max_total_size` reservation
- post-upload `max_total_size` enforcement

Selected files are merged into one `RemoteDeletePlan`. A path can only be selected once in that plan.

If all selected files use the same delete mode, one `rclone delete --files-from ...` command is used. If cleanup rules intentionally mix trash and hard-delete behavior, one delete command is used per required delete mode.

Deletion selection is oldest-first by rclone `ModTime`, and complete files are selected rather than partial file sizes.

## Upload reservation

When both of these are true for an upload destination:

```json
"delete_excess_files": true,
"max_total_size": "500G"
```

the application measures the filtered local upload source with `rclone size --json`.

The reservation calculation is:

```text
managed remote bytes after planned pre-cleanup
+ measured local upload bytes + 1 byte transfer allowance
+ 1 MiB safety headroom
- max_total_size
```

If space must be freed, the oldest complete managed remote files are selected until at least that byte deficit is covered.

The upload also receives:

```text
--max-transfer <measured-local-bytes + 1 byte>
--cutoff-mode CAUTIOUS
```

Identical local source/filter combinations share one concurrent size calculation during the run.

## Requirements

- Python 3 with support for `X | None` type syntax.
- `rclone` available in `PATH` for a normal run.
- Configured rclone remotes.
- Read access to every configured local upload path.
- Write permission for the lock-file parent and delete-list directory.
- Remote permissions required by the configured upload, delete, and optional trash-cleanup operations.

The Python application itself uses only the standard library.

## CLI commands

Show help and every CLI option:

```bash
./rclone-multithreaded-upload.py --help
```

Show the application version:

```bash
./rclone-multithreaded-upload.py --version
```

Validate a config file without creating the lock file and without executing rclone commands:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Run normally:

```bash
./rclone-multithreaded-upload.py --config ./config.json
```

`-c` is the short form of `--config`:

```bash
./rclone-multithreaded-upload.py -c ./config.json
```

The config filename extension is not enforced; the file content must be valid JSON.

## Exit behavior

- `0`: the complete requested run succeeded.
- `1`: config loading failed or at least one required runtime stage failed.
- Existing lock file: the application prints `Lock file exists, exiting.` and exits without starting work.
- SIGINT/SIGTERM: the application attempts to remove the lock it created and exits with `128 + signal number`.

## Configuration

Start from `config.example.json` or the CCTV-oriented `rclone-cctv-config.example.json`.

### Root options

| Option | Required | Meaning |
| --- | --- | --- |
| `delete_min_age` | No | Rclone-style age/duration used by cleanup rules that have `delete_old_files=true`. Default runtime value is `31d`. |
| `lock_file` | No | Single-instance lock-file path. |
| `delete_list_dir` | No | Directory used for generated `--files-from` delete lists. |
| `sleep_after_step` | No | Seconds each eligible remote waits after pre-upload trash cleanup before the upload barrier can complete. Non-negative integer. |
| `thread_limits` | No | Object containing the four worker limits below. |
| `upload_directories` | Yes | Non-empty list of upload destinations. |

### `thread_limits`

| Option | Meaning |
| --- | --- |
| `upload_threads` | Maximum simultaneous upload jobs. |
| `cleanup_threads` | Cleanup-rule planning/deletion worker limit. |
| `remote_quota_cleanup_threads` | Managed `max_total_size` planning/verification worker limit. Because cleanup and quota planning share a snapshot job, combined cleanup stages use the higher of this value and `cleanup_threads`. |
| `trash_cleanup_threads` | Maximum simultaneous `rclone cleanup` jobs. |

Every thread limit must be at least `1`.

### `upload_directories[]`

| Option | Required | Meaning |
| --- | --- | --- |
| `name` | No | Friendly name used in summaries. |
| `local_path` | Yes | Local source directory. |
| `remote_path` | Yes | Rclone destination root, for example `EncryptedDrive:CCTV`. |
| `upload_command` | No | `copy`, `sync`, or `move`. Default `copy`. |
| `delete_old_files` | No | Default age-deletion behavior inherited by cleanup rules that set their override to `null`. Default `true`. |
| `delete_excess_files` | No | Default limit/quota-deletion behavior inherited by cleanup rules. Default `true`. |
| `max_total_size` | No | Maximum total size of the union of files covered by this destination's `cleanup_rules`; `null` disables the remote-wide size cap. |
| `delete_to_trash` | No | Default delete mode inherited by cleanup rules. `false` requests direct/hard deletion where the backend has a supported rclone flag. |
| `empty_trash` | No | When `true` and `delete_to_trash=true`, run `rclone cleanup` at the trash-cleanup stages. Default `true`. |
| `buffer_size` | No | Per-upload rclone `--buffer-size`, for example `64M`; `null` leaves it unset. |
| `cleanup_rules` | No | List of relative managed paths and limits owned by this upload destination. |
| `copy_options` | No | Extra rclone options appended to `copy`, `sync`, or `move`, subject to the restrictions below. |

### `cleanup_rules[]`

Each cleanup rule belongs to exactly one upload destination and its `path` is relative to that destination's `remote_path`.

| Option | Required | Meaning |
| --- | --- | --- |
| `path` | Yes | Relative managed path. `/` means the complete upload root. |
| `max_files` | No | Positive maximum file count; `null` disables this limit. |
| `max_size` | No | Maximum size such as `50G`; `null` disables this limit. |
| `delete_old_files` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |
| `delete_excess_files` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |
| `delete_to_trash` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |

Duplicate normalized cleanup-rule paths inside the same upload destination are rejected.

`max_total_size` only covers files included by that destination's `cleanup_rules`. If `cleanup_rules` is empty, the managed union is empty and `max_total_size` has no managed files to count.

## `copy_options` and local-size filters

The upload command receives `copy_options` as configured.

When reservation sizing is required, only source-selection options that affect which local files are included are forwarded to `rclone size --json`:

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

The application rejects these script-managed flags inside `copy_options`:

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

Use the dedicated config options for the behavior the application manages itself.

## Delete modes

When `delete_to_trash=true`, no hard-delete flag is added and the backend's normal trash behavior is used.

When `delete_to_trash=false`, the application resolves wrapper remotes such as `crypt` through `rclone config dump` and adds a verified backend-specific direct-delete flag when available:

```text
Google Drive -> --drive-use-trash=false
MEGA         -> --mega-hard-delete
OneDrive     -> --onedrive-hard-delete
```

Other backend types receive no backend-specific hard-delete flag from this application.

`empty_trash=true` only causes `rclone cleanup` when `delete_to_trash=true`. When script-managed deletions are direct, the application does not run `rclone cleanup` just to empty unrelated backend trash.

If a backend reports that `rclone cleanup` is unsupported, that condition is treated as a supported skip rather than a failure.

## Rclone commands the application can execute

The application invokes rclone directly without `shell=True`.

### Backend detection

```bash
rclone config dump
```

Used only to resolve wrapper remotes and determine supported direct-delete options. The config dump is parsed in memory and is not intentionally printed.

### Remote snapshots

```bash
rclone lsjson --recursive --files-only --no-mimetype REMOTE:
```

Used for PRE-UPLOAD planning, POST-UPLOAD cleanup planning, and FINAL verification.

### Local reservation sizing

```bash
rclone size LOCAL_PATH --json [source-selection filters]
```

Used only when `delete_excess_files=true` and `max_total_size` is configured for that upload destination.

### Planned deletion

```bash
rclone delete --files-from DELETE_LIST REMOTE: [delete-mode options]
```

Executes the combined delete plan generated from the in-memory snapshot.

### Trash cleanup

```bash
rclone cleanup REMOTE:
```

Runs only when the destination is configured to delete to trash and empty that trash.

### Upload

One of:

```bash
rclone copy LOCAL_PATH REMOTE: [options]
rclone sync LOCAL_PATH REMOTE: [options]
rclone move LOCAL_PATH REMOTE: [options]
```

`sync` can delete destination files that are absent from the selected source according to normal rclone sync semantics. Review the rclone options and test carefully before using it against important data.

## Generated files and lock behavior

The configured lock file prevents two normal instances from running at once. The file contains the process PID and is removed on normal process exit through the registered cleanup handler, and also on handled SIGINT/SIGTERM.

Combined delete plans are written below `delete_list_dir` with names derived from the phase, delete mode, and remote path. They are then passed to `rclone delete --files-from`.

## Final result output

The final summary retains these per-remote stage groups:

```text
Reservation
Upload
Post cleanup
Final quota
```

`Reservation` covers the pre-upload snapshot, cleanup/reservation plan, planned deletion, and pre-upload trash-cleanup prerequisite.

A stage can be `SUCCESS`, `FAILED`, or `SKIPPED`. Captured failure details are printed under the affected remote. The overall result is `SUCCESS` only when every required stage is successful.

## Project files

```text
rclone-multithreaded-upload.py
rclone_multithreaded_upload/
    __init__.py
    cleanup.py
    cli.py
    commands.py
    config.py
    delete_plan.py
    lock.py
    main.py
    models.py
    output.py
    phases.py
    planning.py
    rclone_backend.py
    remote_files.py
    reservation.py
    results.py
    state.py
    summary.py
    targets.py
    upload.py
    utils.py
    verification.py
tests/
    test_logic.py
    test_integration_fake_rclone.py
config.example.json
rclone-cctv-config.example.json
README.md
VERSIONING.md
commented_code_map.md
verification_report.txt
SHA256SUMS
```

For internal function-by-function behavior, see `commented_code_map.md`. For release history, see `VERSIONING.md`.
