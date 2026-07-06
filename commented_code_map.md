# Commented Code Map

Version 0.0.13

This file maps the current implementation in `rclone-multithreaded-upload.py`. It explains what each dataclass, top-level function, application phase, and external rclone command does and why the code uses it.

The application is still a single Python module in v0.0.13. The section headers in the source already divide the responsibilities that can later be extracted into separate modules with limited behavior change.

## High-level call path

```text
main
 |
 +-- parse_cli_args
 +-- load_config
 |    +-- load_json_config
 |    +-- parse_upload_directories
 |         +-- parse_cleanup_rules
 |         +-- validation helpers
 |
 +-- build_cleanup_directories
 +-- initialize_run_results
 +-- acquire_lock                  [normal run only]
 +-- print_startup_summary
 |
 +-- run_cleanup_phase             [PRE-UPLOAD]
 +-- run_trash_cleanup_phase       [PRE-UPLOAD]
 +-- sleep_after_step
 |
 +-- run_reservation_and_upload_phase
 |    +-- reserve_and_upload_one_remote  [one concurrent pipeline per remote]
 |         +-- reserve_one_upload_remote_space
 |         |    +-- get_filtered_local_upload_size
 |         |    +-- make_upload_reservation_delete_list
 |         |    |    +-- get_upload_remote_quota_entries
 |         |    |         +-- get_remote_file_entries
 |         |    +-- rclone delete when space must be freed
 |         |    +-- repeat/verify reservation
 |         +-- cleanup_one_trash_remote  [post-reservation]
 |         +-- upload_one_directory
 |
 +-- run_cleanup_phase             [POST-UPLOAD]
 +-- run_remote_quota_phase        [POST-UPLOAD]
 +-- run_trash_cleanup_phase       [POST-UPLOAD]
 +-- run_final_verification
 +-- print_final_run_result
```

## Dataclasses

### `DirectoryCleanupRule`

**What it stores:** one configured cleanup rule relative to an owning upload destination.

Fields:

- `path` — path relative to `UploadDirectory.remote_path`; `/` means the remote root.
- `max_files` — optional maximum recursive file count.
- `max_size` — optional maximum recursive size.
- `delete_old_files` — optional rule override; `None` means inherit upload default.
- `delete_excess_files` — optional rule override; `None` means inherit upload default.
- `delete_to_trash` — optional rule override; `None` means inherit upload default.

**Why it exists:** it keeps raw rule intent separate from the generated full remote cleanup target and preserves inheritance semantics until target construction.

### `UploadDirectory`

**What it stores:** one local source and one independent rclone destination with upload defaults, total quota settings, cleanup rules, and transfer options.

**Why it exists:** the application operates per destination and must keep every remote's limits, options, reservation state, and cleanup ownership isolated from other remotes.

### `CleanupTarget`

**What it stores:** one fully resolved remote cleanup path with effective inherited booleans and the owning upload remote.

**Why it exists:** cleanup workers should operate on final effective values instead of re-implementing inheritance logic during every cleanup pass.

### `RemoteFile`

**What it stores:** normalized remote-relative `path`, exact byte `size`, and rclone `ModTime` text.

**Why it exists:** destructive cleanup logic needs a small validated representation of each rclone file record.

### `RemoteQuotaFile`

**What it stores:** a managed file relative to an upload destination root plus the cleanup-rule path that made it managed.

**Why it exists:** remote-wide `max_total_size` and reservation logic need files relative to the upload root so one `--files-from` list can be applied to that root.

### `StageRunResult`

**What it stores:** one stage status and unique captured error messages.

**Why it exists:** threaded jobs print live output, but the final summary still needs a stable per-stage result and retained error context.

### `RemoteRunResult`

**What it stores:** the display name, remote path, and four stage results: reservation, upload, post-cleanup, and final quota.

**Why it exists:** the final result is organized per remote instead of only reporting one global success/failure flag.

## Runtime globals and safety state

### `UPLOAD_DIRECTORIES`

Loaded validated upload destinations. Empty until `load_config()` succeeds.

### `CONFIG_PATH`

Resolved config path shown in the startup summary.

### `DELETE_MIN_AGE`

Global minimum age passed to age-based `rclone delete` operations.

### `ALLOWED_UPLOAD_COMMANDS`

Restricts upload commands to `copy`, `sync`, and `move`.

**Why:** prevents an arbitrary destructive rclone command such as `delete` or `purge` from being configured as the upload action.

### Thread-limit globals

- `UPLOAD_THREADS`
- `CLEANUP_THREADS`
- `REMOTE_QUOTA_CLEANUP_THREADS`
- `TRASH_CLEANUP_THREADS`

These independently bound concurrent work by responsibility.

### `OUTPUT_LOCK`

Serializes multi-line worker output.

**Why:** several thread pools can run jobs concurrently; without a shared lock, output blocks could interleave and become difficult to audit.

### `LOCK_FILE`

Single-instance process lock path.

### `DELETE_LIST_DIR`

Directory for generated `--files-from` lists.

### `SLEEP_AFTER_STEP`

Configured inter-phase/per-pipeline delay.

### `RESERVATION_SAFETY_HEADROOM_BYTES`

Fixed 1 MiB reservation margin.

**Why:** avoids accepting an exact quota boundary that can immediately fail because of byte-level source changes or timing around live data.

### `MAX_RESERVATION_CLEANUP_PASSES`

Limits reservation recalculation/deletion to 10 passes.

**Why:** a live source can grow during reservation, but the script must not loop forever against a continuously changing source.

### `lock_created`

Tracks whether this process created the lock.

**Why:** prevents the process from deleting a lock that it did not successfully acquire.

### `RESERVED_UPLOAD_BYTES` and `RESERVED_UPLOAD_BYTES_LOCK`

Per-remote measured source bytes from successful reservation and a lock protecting concurrent access.

**Why:** the later upload needs the exact reserved byte amount to build its `--max-transfer` cap.

### `RUN_RESULTS` and `RUN_RESULTS_LOCK`

Per-remote final result storage and its concurrency lock.

## General helper functions

### `print_step(message)`

**What:** prints a spaced phase/status message with immediate flushing.

**Why:** keeps major sequential steps visible in long-running logs.

### `print_error(message)`

**What:** prints a formatted error message.

**Why:** distinguishes high-level application failures from ordinary worker output.

### `print_job_block(job_type, job_number, target, message)`

**What:** prints one entire worker message block while holding `OUTPUT_LOCK`.

**Why:** prevents concurrent cleanup/reservation/trash threads from mixing their lines.

### `run_command(command, capture_output=False)`

**What:** runs a subprocess from an argument list with `shell=False` semantics.

**Why:** centralizes non-streamed command execution and avoids shell interpolation of config paths/options.

### `parse_size_to_bytes(size_text)`

**What:** converts supported size text such as `500M`, `50G`, or `1.5TB` into binary bytes.

**Why:** cleanup and reservation comparisons must use exact integer byte arithmetic.

### `validate_upload_command(command)`

**What:** normalizes and validates `copy`, `sync`, or `move`.

**Why:** keeps upload configuration inside the intentionally allowed command set.

### `is_directory_not_found(result)`

**What:** searches captured rclone stdout/stderr for a missing-directory condition.

**Why:** a remote folder that does not exist yet can be normal before the first upload and should often be treated as an empty target rather than a fatal cleanup failure.

### `remote_name_from_path(remote_path)`

**What:** converts `:` and `/` in a remote path into underscores.

**Why:** generated delete-list files need filesystem-safe names derived from remote targets.

### `join_rclone_remote_path(remote_root, relative_path)`

**What:** joins an upload remote root with a cleanup-rule path; `/` resolves to the root.

**Why:** cleanup-rule paths are deliberately stored relative to their owning destination.

### `join_relative_path(base, child)`

**What:** joins two normalized relative paths.

**Why:** provides safe relative-path composition for file-list/path comparison logic without adding a leading slash.

### `normalize_relative_path(path)`

**What:** converts backslashes to forward slashes and strips outer `/` characters.

**Why:** equality/prefix comparisons and `--files-from` paths need a consistent remote-relative representation.

### `parse_rclone_modtime(modified)`

**What:** parses rclone RFC3339/ISO modification time, requires a timezone, and converts to UTC.

**Why:** destructive oldest-first ordering must use real chronological time rather than trusting textual ordering across offsets.

### `remote_file_oldest_sort_key(file)`

**What:** returns `(UTC ModTime, relative path)`.

**Why:** provides oldest-first cleanup with a deterministic path tie-breaker.

### `format_bytes(size_bytes)`

**What:** formats bytes as B/KiB/MiB/GiB/TiB.

**Why:** reservation and verification output should remain human-readable without changing internal byte arithmetic.

## Final-result state functions

### `initialize_run_results()`

**What:** creates fresh `RemoteRunResult` objects for all configured destinations.

**Why:** each execution must start with clean stage state and use the configured display name.

### `get_stage_result(remote_path, stage_name)`

**What:** retrieves one stage object dynamically.

**Why:** common stage helpers can work with reservation/upload/post-cleanup/final-quota without duplicated branching.

### `record_stage_success(remote_path, stage_name)`

**What:** marks a stage `SUCCESS` unless already `FAILED`.

**Why:** later aggregate completion must never erase an earlier worker failure.

### `record_stage_failure(remote_path, stage_name, error)`

**What:** marks the stage `FAILED` and stores unique error text.

**Why:** multiple workers/phases can contribute useful context, but exact duplicate retry/stat lines should not flood the final summary.

### `record_stage_skipped(remote_path, stage_name)`

**What:** changes only `PENDING` to `SKIPPED`.

**Why:** a prerequisite failure can skip a later stage without overwriting a stage that already ran or failed.

### `finalize_stage_for_all(stage_name)`

**What:** marks still-pending aggregate stages successful after a complete phase.

**Why:** some stage success is represented by the phase completing without an individual failure callback.

### `mark_pending_stages_skipped()`

**What:** marks every still-pending stage skipped.

**Why:** a global pre-upload safety failure stops later work, and the final summary should distinguish “not run” from “failed”.

### `command_error_summary(output, fallback=...)`

**What:** keeps error-looking lines from command output, deduplicates them, and falls back to the last 20 non-empty lines.

**Why:** final results need useful failure evidence without repeating all live rclone statistics output.

### `remote_result_label(result)`

**What:** derives remote `FAILED`, `SUCCESS`, or `SKIPPED` from its four stage statuses.

**Why:** gives each destination one overall result while preserving stage details.

### `print_final_run_result(exit_code)`

**What:** prints all per-remote stages, retained errors, failed remotes, overall result, and exit code.

**Why:** threaded live output can be long; operators need one final auditable summary.

## Backend and delete-mode functions

### `get_rclone_config_dump()`

**What:** runs `rclone config dump`, parses the JSON object, and caches it once.

**Why:** backend detection needs configured remote metadata. The JSON is captured and never printed because it can contain obscured secrets or tokens.

### `resolve_underlying_backend_type(remote_path)`

**What:** resolves a configured remote through supported wrapper types (`alias`, `chunker`, `compress`, `crypt`, `hasher`) for up to 16 layers and returns the underlying backend type.

**Why:** a crypt remote must use the hard-delete option of its storage backend, not a hard-coded Google Drive flag.

The function also detects wrapper loops and supports rclone on-the-fly backend connection strings beginning with `:`.

### `get_delete_mode_options(target)`

**What:** returns the explicit hard-delete flag for Drive, Mega, or OneDrive when effective `delete_to_trash=false`.

**Why:** these backends have different rclone flags. Unknown backends are left at normal behavior rather than receiving an invented unsafe option.

### `get_delete_mode_text(target)`

**What:** returns a human-readable delete-mode description.

**Why:** startup output should clearly show whether trash/default or hard/direct deletion was requested.

## CLI and configuration functions

### `parse_cli_args()`

**What:** creates the argparse interface for required `--config`, `--validate-config`, `--version`, and standard help.

**Why:** config is mandatory so the script never silently executes with embedded/example remotes.

### `load_json_config(config_path)`

**What:** verifies the config path exists and is a file, then parses JSON and requires an object root.

**Why:** later config parsing assumes named root fields and should fail before destructive work when the file shape is wrong.

### `require_string(section_name, data, key)`

**What:** reads a required non-empty string.

**Why:** centralizes consistent path/name validation and precise config error locations.

### `optional_string(section_name, data, key, default)`

**What:** reads a non-empty string or `None`.

**Why:** supports optional size/name/buffer values while rejecting empty strings that are usually configuration mistakes.

### `optional_bool(section_name, data, key, default)`

**What:** reads a strict boolean.

**Why:** JSON values such as `0`, `1`, or strings should not silently become safety-related booleans.

### `optional_bool_or_none(section_name, data, key)`

**What:** reads `true`, `false`, or `null`.

**Why:** cleanup rules need a third inheritance state in addition to true/false.

### `optional_non_negative_int(section_name, data, key, default)`

**What:** reads an integer `>= 0` and rejects booleans.

**Why:** `sleep_after_step` can be zero, while Python's `bool` subclassing of `int` must not accidentally accept `true`/`false` as numeric settings.

### `optional_positive_int_or_none(section_name, data, key)`

**What:** reads a positive integer or `None`.

**Why:** `max_files=0` is not accepted as an ambiguous “delete everything” rule.

### `optional_string_list(section_name, data, key)`

**What:** reads a list containing only strings.

**Why:** rclone options are passed as an argv list and each element must already be a string.

### `parse_cleanup_rules(upload_section_name, raw_upload)`

**What:** validates one upload's `cleanup_rules`, normalizes paths for duplicate detection, validates limits/overrides, and returns `DirectoryCleanupRule` objects.

**Why:** rule ownership and inheritance must be known before destructive cleanup targets are generated.

### `parse_upload_directories(config)`

**What:** validates the required non-empty upload list, reads all destination settings, validates size fields, rejects script-managed rclone flags in `copy_options`, and creates `UploadDirectory` objects.

**Why:** every runtime worker should receive already validated typed configuration. The rejected flags protect reservation/runtime assumptions from being overridden by raw rclone options.

### `load_config(config_path_text)`

**What:** loads root config, rejects obsolete top-level `directory_cleanup_rules`, parses upload destinations, thread limits, lock/delete-list paths, age, and sleep settings, then updates runtime globals.

**Why:** centralizes the one configuration activation point before targets or workers are created.

## Lock handling functions

### `acquire_lock()`

**What:** creates the lock parent directory and atomically opens the lock with `O_CREAT | O_EXCL`; writes `pid=<pid>`.

**Why:** two simultaneous instances could independently calculate cleanup/quota state and delete/upload against stale assumptions.

A pre-existing lock causes a clean exit with code `0`. Other lock creation errors exit with code `1`.

### `release_lock()`

**What:** removes the lock only when `lock_created` is true.

**Why:** the process must not remove another instance's pre-existing lock.

### `signal_handler(signum, frame)`

**What:** logs the signal, releases the owned lock, and exits with `128 + signum`.

**Why:** SIGINT/SIGTERM should not normally leave the application's own lock behind.

### `sleep_after_step()`

**What:** prints and sleeps for the configured global delay.

**Why:** keeps the deliberate pause between pre-upload safety phases and pipeline startup in one helper.

## Cleanup-target construction and startup output

### `build_cleanup_directories()`

**What:** converts every upload-owned rule into a full `CleanupTarget`, joining paths and resolving boolean inheritance.

**Why:** cleanup workers should receive one explicit target with no need to know the original config nesting.

### `print_startup_summary(cleanup_directories)`

**What:** prints version, config path, execution order, thread limits, global cleanup settings, every upload destination, every raw rule, and every generated target.

**Why:** operators can review the effective destructive configuration before cleanup starts. `--validate-config` uses the same summary without starting remote work.

## Remote listing and delete-list functions

### `get_remote_file_entries(remote_path)`

**What:** runs one recursive files-only `rclone lsjson`, treats a missing directory as empty, parses the JSON array, validates every file's `Path`, `Size`, and timezone-bearing `ModTime`, normalizes paths, and returns `RemoteFile` objects.

**Why:** cleanup ordering and byte accounting are destructive decisions. The application deliberately fails on malformed/incomplete metadata instead of silently continuing with an incomplete remote view.

### `make_delete_list(target)`

**What:** builds the per-rule delete list for `max_files`, `max_size`, or both. Files sort oldest-first and complete files are selected until all configured limits are satisfied.

**Why:** one deterministic calculation and one `rclone delete --files-from` operation are easier to audit than deleting files one-by-one while repeatedly querying the remote.

### `get_upload_remote_quota_entries(upload)`

**What:** reads one recursive listing of `upload.remote_path`, then filters the listing in Python to files covered by any of that upload's cleanup rules. It deduplicates overlapping-rule matches by relative path.

**Why:** reservation and `max_total_size` cleanup need one consistent remote snapshot and must never count/delete the same file twice because rules overlap.

### `make_upload_remote_quota_delete_list(upload)`

**What:** calculates the current managed remote size and selects oldest managed files until size is at or below `max_total_size`.

**Why:** post-upload quota cleanup enforces one destination-wide cap across that destination's managed rule paths.

## Cleanup worker functions

### `cleanup_one_directory(job_number, target, phase_name="")`

**What:** performs one rule cleanup. It can first delete files older than `DELETE_MIN_AGE`, then enforce `max_files`/`max_size` with a generated delete list.

**Why:** age cleanup and excess-limit cleanup are separately configurable but belong to the same effective target and stage accounting.

Phase names beginning with `POST-UPLOAD` record failures in `post_cleanup`; other calls record against `reservation`.

### `cleanup_one_upload_remote_quota(job_number, upload, phase_name="")`

**What:** enforces one upload destination's `max_total_size` across managed rule paths.

**Why:** per-rule limits do not replace the independent destination-wide total size cap.

### `cleanup_one_trash_remote(job_number, upload, phase_name="")`

**What:** conditionally runs `rclone cleanup <remote_path>`.

**Why:** trash-backed deletion can retain provider quota until trash is emptied. The function skips cleanup for direct-delete mode and treats unsupported cleanup backends as a non-fatal skip.

## Local sizing and reservation functions

### `transfer_cap_bytes(transfer_bytes)`

**What:** returns zero for non-positive values, otherwise adds one byte.

**Why:** an upload measured at exactly N bytes should be able to reach an rclone `--max-transfer` cap without boundary ambiguity.

### `validate_local_upload_path(upload)`

**What:** verifies `local_path` exists and is a directory.

**Why:** reservation and upload must fail before remote deletion when the local source root is unavailable or wrong.

### `get_size_filter_options(upload)`

**What:** extracts only source-selection filters from `copy_options`, supporting `--flag value` and `--flag=value` forms.

**Why:** `rclone size` must measure the same candidate source set as the upload without receiving unrelated stats/transfer/runtime options.

Recognized sizing filters:

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

### `get_filtered_local_upload_size(job_number, upload)`

**What:** validates the local source, builds sizing filters, runs `rclone size <local> --json`, parses exact bytes/count, and records reservation failures.

**Why:** reservation deliberately uses the complete filtered local candidate size and does not depend on a source/destination comparison.

### `make_upload_reservation_delete_list(upload, local_upload_bytes)`

**What:** calculates:

```text
required free bytes =
    current managed remote bytes
  + reserved upload byte cap
  + 1 MiB safety headroom
  - max_total_size
```

clamped at zero. It then selects complete oldest managed remote files until selected bytes are at least the deficit.

**Why:** reservation must free enough full-file bytes before upload begins and cannot delete a fraction of the final selected file.

The function returns the delete-list path and accounting values so the caller can report and verify the decision.

### `reserve_one_upload_remote_space(job_number, upload)`

**What:** runs the complete pre-upload reservation loop for one remote.

It:

1. skips quota reservation when `delete_excess_files=false` or `max_total_size=null`;
2. re-sizes the filtered local source every pass;
3. rejects a source that cannot fit even on an empty managed remote with required headroom;
4. calculates a reservation delete list;
5. runs one oldest-file `rclone delete --files-from` when needed;
6. re-reads local and managed remote sizes;
7. verifies the reservation using the latest values;
8. repeats up to `MAX_RESERVATION_CLEANUP_PASSES` if the live source grew or the condition still is not satisfied;
9. stores the measured local bytes in `RESERVED_UPLOAD_BYTES` only after successful reservation.

**Why:** CCTV sources can change while the application is running. Re-reading sizes after each cleanup pass avoids relying on a stale one-time measurement while the pass limit prevents an infinite loop.

## Verification functions

### `verify_one_cleanup_target(job_number, target)`

**What:** re-lists one target and checks final recursive `max_files` and `max_size` limits.

**Why:** successful delete command exit codes do not by themselves prove that provider state now satisfies the configured limit.

### `verify_one_upload_remote_quota(job_number, upload)`

**What:** re-lists managed files and verifies total size is at or below `max_total_size`.

**Why:** the final remote-wide quota condition must be checked from current remote state after upload and cleanup.

## Phase-runner functions

### `run_cleanup_phase(cleanup_directories, phase_name)`

**What:** runs `cleanup_one_directory` concurrently with `CLEANUP_THREADS` and aggregates failures.

**Why:** independent rule targets can be processed in parallel while each worker still records the owning remote's stage state.

### `run_remote_quota_phase(phase_name)`

**What:** runs remote-wide quota cleanup concurrently with `REMOTE_QUOTA_CLEANUP_THREADS`.

**Why:** each upload destination has an independent managed quota and can be cleaned separately.

### `run_trash_cleanup_phase(phase_name)`

**What:** runs per-remote trash cleanup concurrently with `TRASH_CLEANUP_THREADS`.

**Why:** provider cleanup can be slow and independent across destinations.



### `reserve_and_upload_one_remote(job_number, upload)`

**What:** runs one remote's reservation, post-reservation trash cleanup, optional delay, and upload in sequence.

**Why:** a remote that finishes reservation should be allowed to start uploading without waiting for unrelated remotes to reserve space. A reservation or post-reservation trash failure skips only that remote's upload.

### `run_reservation_and_upload_phase()`

**What:** runs independent `reserve_and_upload_one_remote` pipelines concurrently using `UPLOAD_THREADS`.

**Why:** preserves per-remote ordering while allowing separate remotes to progress independently.

### `run_final_verification(cleanup_directories)`

**What:** verifies all cleanup targets concurrently, then verifies all upload-level managed quotas concurrently, finalizes pending final-quota stages, and returns aggregate success.

**Why:** both rule-level and destination-wide limits are separate configured guarantees and must be checked at the end.

## Upload output and command functions

### `print_thread_output(thread_number, remote_path, line)`

**What:** prints one upload output line in an `UPLOAD JOB` block under `OUTPUT_LOCK`.

**Why:** streamed rclone output remains attributable to the correct remote even with concurrent uploads.

### `run_command_streamed(command, thread_number, remote_path)`

**What:** starts a subprocess with merged stdout/stderr, streams each line live, retains all lines, and returns `(return_code, captured_output)`.

**Why:** long rclone uploads need live progress visibility, while failed commands still need retained error context for the final summary.

### `get_upload_buffer_options(upload)`

**What:** returns `[]` or `['--buffer-size', value]`.

**Why:** the script owns `--buffer-size` as a dedicated per-destination setting and therefore constructs it separately from raw `copy_options`.

### `upload_one_directory(job_number, upload)`

**What:** validates the local directory and upload command, builds backend delete options for `sync`, appends configured upload options and buffer size, adds reservation transfer-cap options when applicable, streams the rclone command, and records the upload result.

**Why:** this is the one place where a validated `UploadDirectory` becomes the final rclone upload argv. Keeping command construction centralized reduces differences between `copy`, `sync`, and `move` behavior.

## Main entry point

### `main()`

**What:** orchestrates the entire application and returns the process exit code.

Detailed order:

1. Parse CLI.
2. Load and validate config; return `1` on config failure.
3. Build effective cleanup targets.
4. Initialize per-remote results.
5. For normal runs, register lock cleanup, install SIGINT/SIGTERM handlers, and acquire the lock.
6. Print startup summary.
7. For `--validate-config`, print success and return `0` before any remote command.
8. Run pre-upload cleanup rules; abort later work and summarize on failure.
9. Run pre-upload trash cleanup; abort later work and summarize on failure.
10. Sleep for the configured global delay.
11. Run independent reservation/upload pipelines.
12. Always run post-upload cleanup rules, remote quota cleanup, trash cleanup, and final verification even when an upload pipeline failed.
13. Print high-level phase errors.
14. Return `0` only when upload pipelines and every required post/final phase succeeded; otherwise return `1`.
15. Print the final per-remote result before returning.

**Why:** destructive pre-upload safety failures stop uploads, while upload failures do not suppress post-upload cleanup because partial data may already have reached the remote.

## External rclone commands used by the code

### `rclone config dump`

Called by `get_rclone_config_dump()`.

**Purpose:** obtain configured remote metadata for wrapper/backend resolution.

**Why:** backend-specific hard-delete flags differ and crypt/wrapper remotes hide the real storage type in the visible remote path.

The parsed config is cached and not printed.

### `rclone lsjson --recursive --files-only --no-mimetype <remote>`

Called by `get_remote_file_entries()`.

**Purpose:** obtain one recursive JSON array of file paths, exact sizes, and modification times.

**Why:** all destructive oldest-first and byte-accounting decisions are made in Python from validated structured metadata.

### `rclone delete <target> --min-age <age> [delete-mode options]`

Called by `cleanup_one_directory()` when effective `delete_old_files=true`.

**Purpose:** delete files older than the configured minimum age.

**Why:** rclone already provides the age filter and can perform the bulk age-based delete directly.

### `rclone delete --files-from <list> <target> [delete-mode options]`

Called by:

- `cleanup_one_directory()` for per-rule `max_files`/`max_size` cleanup;
- `cleanup_one_upload_remote_quota()` for post-upload `max_total_size` cleanup;
- `reserve_one_upload_remote_space()` for pre-upload quota reservation.

**Purpose:** delete exactly the complete files selected by Python.

**Why:** Python owns ordering and limit arithmetic; rclone performs one bulk deletion using the calculated relative path list.

### `rclone cleanup <remote_path>`

Called by `cleanup_one_trash_remote()`.

**Purpose:** empty remote trash/recycle storage when configured.

**Why:** provider trash can continue using quota after deletion.

Unsupported cleanup is treated as a non-fatal skip.

### `rclone size <local_path> --json [source-selection filters]`

Called by `get_filtered_local_upload_size()`.

**Purpose:** measure exact bytes and count for the complete local candidate set selected by upload filters.

**Why:** reservation must know the amount of data that may be uploaded before deleting remote files to create space.

### `rclone copy <local> <remote> ...`

Built by `upload_one_directory()` when `upload_command="copy"`.

**Purpose:** copy selected local files without deleting destination-only files.

### `rclone sync <local> <remote> ...`

Built by `upload_one_directory()` when `upload_command="sync"`.

**Purpose:** synchronize destination contents to the filtered source semantics of rclone.

**Safety note:** `sync` is inherently more destructive than `copy`. When upload-level `delete_to_trash=false`, supported backend-specific hard-delete options are added to the sync command.

### `rclone move <local> <remote> ...`

Built by `upload_one_directory()` when `upload_command="move"`.

**Purpose:** transfer selected source files and remove successfully moved source files according to rclone behavior.

### Upload reservation cap options

When a positive reserved byte amount exists for a destination with `max_total_size`, `upload_one_directory()` adds:

```text
--max-transfer <reserved bytes + 1>B
--cutoff-mode CAUTIOUS
```

**Why:** files appearing or growing after reservation cannot silently consume quota that was not reserved.

### Upload buffer option

When `buffer_size` is configured, uploads receive:

```text
--buffer-size <configured size>
```

**Why:** buffer memory can be tuned independently for each remote and is intentionally owned by the script rather than raw `copy_options`.

## Natural module boundaries for a future refactor

The current source headers already reveal low-risk extraction boundaries:

```text
rclone_multithreaded_upload/
├── __init__.py
├── cli.py
├── config.py
├── models.py
├── output.py
├── results.py
├── lock.py
├── rclone_backend.py
├── remote_files.py
├── cleanup.py
├── reservation.py
├── upload.py
├── phases.py
└── main.py
```

A safer incremental extraction order is:

1. `models.py` — dataclasses only.
2. `config.py` — config parsing/validation and config dataclasses.
3. `output.py` plus `results.py` — no destructive behavior.
4. `rclone_backend.py` plus `remote_files.py` — command/listing helpers.
5. `cleanup.py` — per-rule, quota, trash, and verification logic.
6. `reservation.py` — local sizing and space reservation.
7. `upload.py` — upload argv construction and streamed execution.
8. `phases.py` — thread-pool orchestration.
9. `main.py`/`cli.py` — thin application entry point.

The major rule for that refactor should be **move existing functions before redesigning them**. Each module version should first preserve function signatures and behavior, then run compile, CLI, config-validation, import, and command-construction parity checks before any algorithm cleanup is attempted.
