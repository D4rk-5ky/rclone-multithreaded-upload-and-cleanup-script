# Commented Code Map

Version 0.0.19

v0.0.19 keeps the modular package layout and changes the remote workflow from repeated live re-listing to three per-remote snapshots.

## High-level call path

```text
rclone-multithreaded-upload.py
  -> rclone_multithreaded_upload.main.main
     -> cli.parse_cli_args
     -> config.load_config
     -> targets.build_cleanup_directories
     -> results.initialize_run_results
     -> lock.acquire_lock
     -> summary.print_startup_summary
     -> phases.run_reservation_and_upload_phase
        -> one reserve_and_upload_one_remote worker per remote
           -> remote_files.fetch_remote_snapshot              [PRE snapshot]
           -> reservation.get_filtered_local_upload_size      [single-flight cache]
           -> planning.build_pre_upload_plan
              -> planning.plan_cleanup_targets
                 -> age from snapshot ModTime
                 -> max_files / max_size
              -> planning.plan_upload_reservation
           -> delete_plan.execute_delete_plan
           -> cleanup.cleanup_one_trash_remote
           -> upload.upload_one_directory
     -> phases.run_post_upload_cleanup_phase
        -> one post_cleanup_one_remote worker per remote
           -> remote_files.fetch_remote_snapshot              [POST snapshot]
           -> planning.build_post_upload_plan
              -> planning.plan_cleanup_targets
              -> planning.plan_remote_quota_cleanup
           -> delete_plan.execute_delete_plan
           -> cleanup.cleanup_one_trash_remote
     -> phases.run_final_verification
        -> one verify_one_remote worker per remote
           -> remote_files.fetch_remote_snapshot              [FINAL snapshot]
           -> verification.verify_upload_snapshot
     -> results.print_final_run_result
```

## `models.py`

Shared dataclasses:

- `DirectoryCleanupRule`
- `UploadDirectory`
- `CleanupTarget`
- `RemoteFile`
- `RemoteQuotaFile` — compatibility model for managed-list callers
- `RemoteSnapshot`
- `PlannedDeletion`
- `RemoteDeletePlan`
- `StageRunResult`
- `RemoteRunResult`

`RemoteSnapshot.files_by_path` is keyed by normalized path relative to `UploadDirectory.remote_path`.

`RemoteDeletePlan.entries` is keyed by the same relative path, so one file cannot be selected twice in one phase.

## `remote_files.py`

### `get_remote_file_entries(remote_path)`

Runs:

```text
rclone lsjson --recursive --files-only --no-mimetype REMOTE
```

Validates the JSON array and `Path`, `Size`, and `ModTime` fields.

### `fetch_remote_snapshot(remote_path)`

Converts one recursive listing into `RemoteSnapshot`.

This is called exactly three times per successful remote by the normal application path.

### `clone_remote_snapshot(snapshot)`

Creates the mutable planning copy.

### `relative_cleanup_target_path(upload, target)`

Maps a generated full cleanup target back to its relative path below the upload root.

### `files_below_relative_path(snapshot, relative_path)`

Filters the current in-memory working snapshot for one cleanup rule.

### `get_managed_snapshot_files(upload, snapshot)`

Returns the deduplicated union of files covered by `upload.cleanup_rules`.

No remote command is executed by the filtering helpers.

## `planning.py`

All planning is in-memory.

### `cleanup_targets_for_upload(...)`

Returns effective cleanup targets owned by one upload remote.

### `plan_cleanup_targets(...)`

For each effective target, in config order:

1. selects files older than `STATE.delete_min_age` from snapshot `ModTime`;
2. removes selected age files from the working snapshot;
3. calculates `max_files` and `max_size` from remaining files;
4. selects oldest complete files until both limits pass;
5. removes every selected path from the working snapshot immediately.

### `plan_remote_quota_cleanup(...)`

Calculates managed `max_total_size` from the already-mutated working snapshot and selects oldest complete managed files until the limit passes.

### `plan_upload_reservation(...)`

Calculates:

```text
current managed bytes
+ local transfer cap
+ 1 MiB headroom
- max_total_size
```

If positive, selects oldest complete managed files until selected bytes are at least the exact deficit.

No remote re-read occurs after selection.

### `build_pre_upload_plan(...)`

Uses one pre-upload snapshot for age cleanup, rule limits, and upload reservation.

### `build_post_upload_plan(...)`

Uses one post-upload snapshot for age cleanup, rule limits, and `max_total_size` cleanup.

## `delete_plan.py`

### `add_planned_deletion(...)`

Adds a path once and immediately removes it from the working snapshot.

The first rule to select a file determines its delete mode, matching sequential planning semantics.

### `planned_delete_bytes(plan)`

Totals selected bytes.

### `print_delete_plan_summary(...)`

Prints files, bytes, and selection reason counts.

### `execute_delete_plan(...)`

Groups plan entries by `delete_to_trash` only when required.

For each mode it writes one `--files-from` list and runs one `rclone delete` against the upload root.

Current supplied GDrive, MEGA, and OneDrive configuration produces one delete mode per remote and therefore at most one combined delete command per snapshot cleanup phase when deletion is needed.

## `reservation.py`

### `get_size_filter_options(upload)`

Extracts only source-selection filters from upload options.

### `local_size_cache_key(upload)`

Key:

```text
resolved local path + ordered source-selection filters
```

### `_calculate_filtered_local_upload_size(upload)`

Runs the actual `rclone size --json` command.

### `get_filtered_local_upload_size(...)`

Uses a `Future`-based single-flight cache.

The first pipeline for a key performs the scan. Concurrent pipelines with the same key wait for and reuse that exact result.

### `transfer_cap_bytes(...)`

Preserves the one-byte allowance.

## `cleanup.py`

Exports the combined delete-plan executor and keeps optional `rclone cleanup` / empty-trash handling.

## `phases.py`

### `reserve_and_upload_one_remote(...)`

Per remote:

```text
PRE snapshot -> plan clean/reservation -> combined delete -> trash -> sleep -> upload
```

### `run_reservation_and_upload_phase(...)`

Starts independent remote pipelines using `upload_threads`.

There is no global snapshot/reservation barrier.

### `post_cleanup_one_remote(...)`

Per remote:

```text
POST snapshot -> plan all cleanup/quota rules -> combined delete -> trash
```

### `run_post_upload_cleanup_phase(...)`

Runs post-cleanup workers concurrently.

### `verify_one_remote(...)`

Fetches the final snapshot and passes every rule for that remote to the single snapshot verifier.

### `run_final_verification(...)`

Runs final per-remote verification concurrently.

## `verification.py`

### `verify_upload_snapshot(...)`

From one final snapshot, verifies:

- every effective `max_files` limit;
- every effective `max_size` limit;
- the upload's managed `max_total_size` limit.

The function performs no remote listing itself.

## `upload.py`

Upload construction remains intentionally conservative.

When a reservation size exists, the upload adds:

```text
--max-transfer <reserved local bytes + 1 byte>B
--cutoff-mode CAUTIOUS
```

The configured `copy`, `sync`, or `move`, source filters, buffer size, and backend-specific sync delete options remain in the upload command.

## Normal recursive remote listing count

Successful normal path for each remote:

```text
get_remote_file_entries PRE   = 1 lsjson
get_remote_file_entries POST  = 1 lsjson
get_remote_file_entries FINAL = 1 lsjson
-----------------------------------------
TOTAL                         = 3 lsjson
```

No age cleanup command performs a separate remote traversal.

No reservation retry loop performs additional remote listings.

No cleanup target is individually re-listed during final verification.

This count is about rclone recursive listing commands. Provider pagination may require multiple API transactions inside one command.
