# Commented Code Map

Version 0.0.18

v0.0.18 completes the module extraction started in v0.0.14. The seven dataclasses remain in `models.py`; executable logic is now divided by responsibility. The compatibility executable contains no application function definitions and calls `rclone_multithreaded_upload.main.main()`.

## Module layout

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

## Structural preservation check

The v0.0.17 baseline had 72 top-level application functions in the executable. v0.0.18 keeps 72 top-level application functions across the package modules and leaves 0 application function definitions in `rclone-multithreaded-upload.py`.

The module split therefore moves the executable functions rather than replacing the application with a redesigned workflow.

## High-level call path

```text
entry point
  -> main.main
     -> cli.parse_cli_args
     -> config.load_config
     -> targets.build_cleanup_directories
     -> results.initialize_run_results
     -> lock.acquire_lock                    [normal run only]
     -> summary.print_startup_summary
     -> phases.run_cleanup_phase             [PRE-UPLOAD]
     -> phases.run_trash_cleanup_phase       [PRE-UPLOAD]
     -> lock.sleep_after_step
     -> phases.run_reservation_and_upload_phase
        -> one reserve_and_upload_one_remote worker per upload remote
           -> reservation.reserve_one_upload_remote_space
              -> reservation.get_filtered_local_upload_size
              -> remote_files.get_upload_remote_quota_entries
                 -> remote_files.get_remote_file_entries
              -> reservation.make_upload_reservation_delete_list
              -> rclone delete when deficit > 0
              -> repeat/re-read up to 10 passes
           -> cleanup.cleanup_one_trash_remote
           -> configured per-remote sleep
           -> upload.upload_one_directory
     -> phases.run_cleanup_phase             [POST-UPLOAD]
     -> phases.run_remote_quota_phase        [POST-UPLOAD]
     -> phases.run_trash_cleanup_phase       [POST-UPLOAD]
     -> phases.run_final_verification
     -> results.print_final_run_result
```

## `state.py`

### `RuntimeState`

Holds runtime values that the old single-file application stored in module globals:

- parsed upload destinations;
- config path;
- delete minimum age;
- four thread limits;
- lock path;
- delete-list directory;
- configured sleep;
- 1 MiB reservation safety headroom;
- 10-pass reservation limit;
- lock-created ownership flag;
- per-remote reserved upload bytes and lock;
- per-remote final result state and lock.

### `STATE`

One shared `RuntimeState` instance. Modules always access `STATE.field` at call time. This prevents copied scalar/path imports from becoming stale after `load_config()` changes runtime values.

## `models.py`

The seven v0.0.14 dataclasses are unchanged in purpose:

- `DirectoryCleanupRule`
- `UploadDirectory`
- `CleanupTarget`
- `RemoteFile`
- `RemoteQuotaFile`
- `StageRunResult`
- `RemoteRunResult`

No rclone command execution lives in this module.

## `output.py`

### `print_step(message)`
Prints one application phase line.

### `print_error(message)`
Prints one visually indented error line.

### `print_job_block(job_type, job_number, target, message)`
Uses the shared output lock so concurrent job blocks do not interleave.

## `results.py`

### `initialize_run_results()`
Creates one fresh `RemoteRunResult` per configured destination.

### `get_stage_result(remote_path, stage_name)`
Returns one stage object.

### `record_stage_success(...)`
Marks a non-failed stage successful.

### `record_stage_failure(...)`
Marks a stage failed and stores unique error text.

### `record_stage_skipped(...)`
Marks only a pending stage skipped.

### `finalize_stage_for_all(stage_name)`
Converts remaining pending aggregate stages to success after their phase finishes.

### `mark_pending_stages_skipped()`
Used when global pre-upload safety phases abort the run.

### `command_error_summary(output, fallback)`
Keeps error-bearing output lines, or final command lines when no error-pattern line exists.

### `remote_result_label(result)`
Returns `FAILED`, `SUCCESS`, or `SKIPPED` from the four stage states.

### `print_final_run_result(exit_code)`
Prints per-remote stages, retained errors, failed remotes, and exit code.

## `config.py`

### `load_json_config(config_path)`
Loads the external JSON object.

### Validation helpers

- `require_string`
- `optional_string`
- `optional_bool`
- `optional_bool_or_none`
- `optional_non_negative_int`
- `optional_positive_int_or_none`
- `optional_string_list`

### `parse_cleanup_rules(section_name, raw_upload)`
Builds upload-owned cleanup rules and rejects duplicate normalized paths.

### `parse_upload_directories(config)`
Builds `UploadDirectory` objects, validates `copy`/`sync`/`move`, size fields, buffer size, and script-managed rclone flags.

### `load_config(config_path_text)`
Parses the full config and updates `STATE` only after validation succeeds.

## `utils.py`

Pure helpers:

- `parse_size_to_bytes`
- `validate_upload_command`
- `remote_name_from_path`
- `join_rclone_remote_path`
- `join_relative_path`
- `normalize_relative_path`
- `parse_rclone_modtime`
- `remote_file_oldest_sort_key`
- `format_bytes`

UTC modification-time parsing remains mandatory before chronological deletion sorting.

## `commands.py`

### `run_command(command, capture_output=False)`
Runs a subprocess without `shell=True` and optionally captures stdout/stderr.

### `is_directory_not_found(result)`
Detects the existing rclone `directory not found` condition.

## `rclone_backend.py`

### `get_rclone_config_dump()`
Runs `rclone config dump`, caches the parsed object, and never prints the captured config.

### `resolve_underlying_backend_type(remote_path)`
Unwraps `alias`, `chunker`, `compress`, `crypt`, and `hasher` remotes, including on-the-fly backend strings. Loop protection and a 16-layer maximum remain.

### `get_delete_mode_options(target)`
Maps direct-delete requests to:

```text
drive    -> --drive-use-trash=false
mega     -> --mega-hard-delete
onedrive -> --onedrive-hard-delete
```

### `get_delete_mode_text(target)`
Returns the human-readable startup-summary delete mode.

## `targets.py`

### `build_cleanup_directories()`
Converts every upload-owned cleanup rule into a full `CleanupTarget`. `None` boolean overrides inherit the owning upload value. The owner remote path is retained for stage accounting.

## `remote_files.py`

### `get_remote_file_entries(remote_path)`
Runs:

```text
rclone lsjson --recursive --files-only --no-mimetype <remote>
```

Validates that the result is an array of files with non-empty `Path`, non-negative integer `Size`, and timezone-aware parseable `ModTime`.

### `get_upload_remote_quota_entries(upload)`
Reads one recursive listing from `upload.remote_path`, filters it to paths managed by `cleanup_rules`, and deduplicates overlapping rule coverage by relative path.

### `make_delete_list(target)`
Sorts target files oldest-first and selects complete files until both `max_files` and `max_size` are satisfied.

### `make_upload_remote_quota_delete_list(upload)`
Sorts managed upload files oldest-first and selects complete files until `max_total_size` is satisfied.

## `cleanup.py`

### `cleanup_one_directory(job_number, target, phase_name)`
Runs optional age cleanup and optional max-files/max-size cleanup. Failures are recorded against reservation or post-cleanup based on phase name.

Age cleanup command:

```text
rclone delete <target> --min-age <age> [backend delete option]
```

Excess cleanup command:

```text
rclone delete --files-from <list> <target> [backend delete option]
```

### `cleanup_one_upload_remote_quota(job_number, upload, phase_name)`
Enforces upload-level `max_total_size` with one oldest-file delete list.

### `cleanup_one_trash_remote(job_number, upload, phase_name)`
Runs `rclone cleanup` only when `empty_trash=true` and script-managed deletions use trash. Unsupported provider cleanup is treated as a skip, matching prior behavior.

## `reservation.py`

### `transfer_cap_bytes(transfer_bytes)`
Returns zero for non-positive input; otherwise adds one byte.

### `validate_local_upload_path(upload)`
Requires an existing local directory.

### `get_size_filter_options(upload)`
Copies only source-selection filters from upload options to `rclone size`.

### `get_filtered_local_upload_size(job_number, upload)`
Runs:

```text
rclone size <local_path> --json [source filters]
```

Parses `bytes` and `count` with `int()` as in v0.0.17 and rejects negative values.

### `make_upload_reservation_delete_list(upload, local_upload_bytes)`
Calculates the exact reservation deficit, sorts managed files oldest-first, and selects complete files until selected bytes meet or exceed the deficit.

### `reserve_one_upload_remote_space(job_number, upload)`
Preserves the live-source reservation loop:

1. skip reservation when excess deletion or total quota is disabled;
2. re-size the filtered local source every pass;
3. reject a local source that cannot fit on an empty managed remote with safety headroom;
4. list managed remote files and calculate the exact deficit;
5. delete one oldest-file list when required;
6. loop and re-read live local/remote sizes;
7. stop after 10 passes rather than loop forever;
8. store measured local bytes only after successful reservation.

## `upload.py`

### `print_thread_output(thread_number, remote_path, line)`
Serializes one upload output line under the output lock.

### `run_command_streamed(command, thread_number, remote_path)`
Starts the upload subprocess with stderr merged into stdout, streams each line live, retains all output, and returns `(return_code, captured_output)`.

### `get_upload_buffer_options(upload)`
Returns the optional `--buffer-size` pair.

### `upload_one_directory(job_number, upload)`
Builds and executes the configured `copy`, `sync`, or `move` command. `sync` receives direct-delete backend flags where configured. A positive successful reservation receives:

```text
--max-transfer <reserved bytes + 1>B --cutoff-mode CAUTIOUS
```

Upload failures retain command context for the final result.

## `verification.py`

### `verify_one_cleanup_target(job_number, target)`
Re-lists one cleanup target and verifies final recursive `max_files` and `max_size` values.

### `verify_one_upload_remote_quota(job_number, upload)`
Re-lists managed upload files and verifies final `max_total_size`.

## `phases.py`

### `run_cleanup_phase(cleanup_directories, phase_name)`
Runs cleanup targets concurrently with `STATE.cleanup_threads`.

### `run_remote_quota_phase(phase_name)`
Runs upload-level total-quota cleanup concurrently with `STATE.remote_quota_cleanup_threads`.

### `run_trash_cleanup_phase(phase_name)`
Runs per-remote trash cleanup concurrently with `STATE.trash_cleanup_threads`.

### `reserve_and_upload_one_remote(job_number, upload)`
Preserves one remote's strict internal order:

```text
reservation
  -> post-reservation trash cleanup
  -> per-remote sleep
  -> upload
```

A reservation or post-reservation trash failure skips only that remote's upload.

### `run_reservation_and_upload_phase()`
Starts independent remote pipelines concurrently with `STATE.upload_threads`.

There is no global reservation barrier. One remote may already delete or upload while another remote is still running `rclone size` or `rclone lsjson`.

### `run_final_verification(cleanup_directories)`
Verifies cleanup targets and upload-level total quotas with their respective thread pools, records crashes as final-quota failures, then finalizes pending final-quota stages.

## `summary.py`

### `print_startup_summary(cleanup_directories)`
Prints the effective execution order, thread limits, reservation constants, upload destinations, cleanup-rule inheritance values, and generated cleanup targets.

## `lock.py`

### `acquire_lock()`
Creates the lock atomically and records ownership.

### `release_lock()`
Removes the lock only when this process created it.

### `signal_handler(signum, frame)`
Prints the signal, releases the lock, and exits `128 + signum`.

### `sleep_after_step()`
Prints and sleeps for the configured global delay.

## `main.py`

### `main()`
Owns top-level phase ordering and exit-code aggregation only. It deliberately still runs global post-upload cleanup and final verification after reservation/upload pipeline failures because an upload can fail after partial transfer.

## Compatibility entry point

`rclone-multithreaded-upload.py` contains no application function definitions. It imports `main` from the package and exits with the returned code.

## Regression verification

`tests/test_logic.py` is non-destructive and mocks remote execution where required. The release tests cover the reservation calculations and the no-global-barrier concurrency property in addition to compile/import/CLI/config validation.
