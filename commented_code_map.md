# Commented code map

This file documents the current application structure, every top-level application function/class, the important shared state, and every external command the program can execute.

The central design rule is:

```text
workers inside one stage may finish independently
but the next stage does not begin until every worker in the current stage has finished
```

A failure is retained for the affected remote. Other remotes continue. A remote whose prerequisite safety stage failed is not allowed to proceed into the dependent upload/trash action.

## `rclone-multithreaded-upload.py`

Compatibility executable entry point.

```python
from rclone_multithreaded_upload.main import main
sys.exit(main())
```

Why: keeps the original executable filename stable while all real logic remains inside the package.

## `rclone_multithreaded_upload/__init__.py`

### `VERSION` / `__version__`

Single application version source. CLI `--version` imports this value so the displayed version and packaged release version stay aligned.

## `models.py`

### `MqttConfig`

Stores the optional broker, topic, authentication, QoS, retain, keepalive, TLS, CA, client-ID, and publish-timeout settings for one final result message.

Why: MQTT notification settings stay typed and separate from destructive rclone/cleanup configuration, and disabled MQTT carries no runtime dependency.

### `DirectoryCleanupRule`

Represents one relative cleanup rule inside one upload destination.

Why: a cleanup path belongs to a specific remote root, preventing an ambiguous global cleanup rule from being applied to the wrong remote.

### `UploadDirectory`

Represents one local source plus its rclone destination and all per-destination options.

Why: every upload destination is handled as an independent unit for result tracking and worker scheduling.

### `CleanupTarget`

Resolved full remote cleanup path generated from an `UploadDirectory` plus a relative `DirectoryCleanupRule`.

Why: planning functions can work with explicit full targets while config stays readable with relative paths.

### `LocalUploadFile`

Immutable representation of one filtered local candidate returned by local `rclone lsjson`: path, size, and modification time.

Why: quota-managed uploads need exact complete-file identities, not only an aggregate byte count.

### `LocalUploadSnapshot`

Immutable tuple of filtered `LocalUploadFile` entries plus the aggregate candidate bytes. `file_count` is derived from the tuple.

Why: one local scan can drive sizing, newest-first cap selection, reservation, and the final exact upload list.

### `LocalUploadSnapshot.file_count`

Read-only property returning the number of candidate files in the snapshot.

Why: callers can report candidate count without storing a second mutable count that could diverge from the exact file tuple.

### `RemoteFile`

Immutable representation of one recursive `rclone lsjson` file entry: path, size, modification time.

Why: cleanup/quota decisions can be calculated from one live snapshot instead of repeatedly listing the provider.

### `RemoteQuotaFile`

Compatibility model containing path, size, modification time, and the source cleanup folder that manages the file.

Why: retains compatibility with the older helper interface while the current planner mainly uses `RemoteFile`.

### `RemoteSnapshot`

In-memory mapping of normalized relative path to `RemoteFile` for one remote root.

Why: one recursive listing can be reused by all cleanup decisions in a phase.

### `PlannedDeletion`

Stores one selected file, its delete mode, and the reason it was selected.

Why: the delete executor can preserve trash/hard-delete choices and the final output can describe why files were selected.

### `RemoteDeletePlan`

Combined planned deletions for one remote and one phase.

Why: age, folder limits, quota cleanup, and reservation can merge selections instead of issuing separate repeated delete traversals.

### `StageRunResult`

Stores `PENDING`, `SUCCESS`, `FAILED`, or `SKIPPED` plus retained error text for one logical stage.

Why: failures must survive until the final summary even when later stages continue.

### `RemoteRunResult`

Contains the four final summary groups for one remote: reservation, upload, post cleanup, final quota. It also retains three internal trash-activity markers: successful reservation-plan trash deletion, an actually started trash-mode upload `sync`, and successful post-cleanup-plan trash deletion.

Why: one failed remote can be reported without losing status for the other remotes, and later trash cleanup can distinguish script-managed trash activity from hard-delete-only work.

## `state.py`

### `RuntimeState`

Holds config-loaded runtime values and concurrency-safe shared dictionaries/locks.

Important fields include:

- `script_name`
- `mqtt`
- `upload_directories`
- `delete_min_age`
- `upload_threads`
- `cleanup_threads`
- `remote_quota_cleanup_threads`
- `trash_cleanup_threads`
- `lock_file`
- `delete_list_dir`
- `sleep_after_step`
- `reservation_safety_headroom_bytes`
- `reserved_upload_bytes`
- `planned_upload_files`
- `run_results`

Why: imported modules all see the same live state after config loading instead of copying stale scalar values.

`STATE` is the single shared instance used by the application.

## `cli.py`

### `FullHelpArgumentParser`

Small `argparse.ArgumentParser` subclass used by the application CLI.

Why: standard argparse errors normally print only a short `usage:` line. This subclass makes bad/misspelled CLI input self-documenting by printing the same complete flag explanations shown by `--help` before the error message.

### `FullHelpArgumentParser.error(message)`

Prints the complete parser help to standard error, then exits with CLI parsing status `2` and the specific argparse error.

Why: users who mistype a flag can immediately see every valid alternative and what each one does without running a second command.

### `build_cli_parser()`

Constructs the CLI parser, its examples, and all supported flags in one place.

Supported CLI options:

- `-h`, `--help` — print complete usage, all flags, explanations, examples, and exit.
- `-c PATH`, `--config PATH` — required config path for normal execution or validation.
- `--validate-config` — load/validate config and print the startup summary without lock creation or rclone execution.
- `--version` — print the package version and exit without requiring a config file.

Why: keeping parser construction separate makes the full help text testable and ensures the normal parser and error parser cannot drift apart.

### `parse_cli_args(argv=None)`

Builds the parser and parses either the real process arguments or an explicitly supplied argument sequence.

Why: the application gets normal argparse behavior while tests can exercise exact argument combinations without changing global process state. Any parse error goes through `FullHelpArgumentParser.error()` and therefore includes the complete help text.

## `config.py`

### `load_json_config(config_path)`

Checks that the path exists and is a file, parses UTF-8 JSON, and requires a JSON object at the root.

Why: malformed or wrong-type config must fail before lock/rclone work.

### `require_string(section_name, config, key)`

Reads a mandatory non-empty string.

Why: required paths/remote names cannot silently become empty values.

### `optional_string(section_name, config, key, default)`

Reads an optional non-empty string or `null`.

Why: supports nullable settings such as `max_total_size` and `buffer_size` while rejecting accidental empty strings.

### `optional_bool(section_name, config, key, default)`

Reads an optional strict JSON boolean.

Why: avoids interpreting strings/numbers as booleans.

### `optional_bool_or_none(section_name, config, key)`

Reads `true`, `false`, or `null`.

Why: cleanup-rule overrides use `null` to inherit the upload-level default.

### `optional_non_negative_int(section_name, config, key, default)`

Reads a non-negative integer.

Why: used for sleeps/thread values before the separate minimum-thread checks.

### `optional_int_in_range(section_name, config, key, default, minimum, maximum)`

Reads a strict integer inside an inclusive range.

Why: MQTT port, QoS, keepalive, and publication timeout each have explicit valid numeric ranges and boolean values must not be accepted as integers.

### `optional_positive_int_or_none(section_name, config, key)`

Reads a positive integer or `null`.

Why: `max_files=0` would be a surprising destructive configuration, so only positive limits are accepted.

### `optional_string_list(section_name, config, key)`

Reads a list containing only strings.

Why: rclone option arrays must remain tokenized safely without shell parsing.

### `parse_mqtt_config(config)`

Builds a fresh `MqttConfig`, validates enabled-broker requirements, numeric ranges, username/password dependency, and TLS option consistency.

Why: malformed notification settings fail during normal config loading/`--validate-config` before the script starts destructive or network transfer work.

### `parse_cleanup_rules(section_name, raw_upload)`

Parses each upload-owned cleanup rule, normalizes its path, rejects duplicate normalized paths, validates optional size limits, and preserves nullable overrides.

Why: cleanup ownership and inheritance are resolved deterministically.

### `parse_upload_directories(config)`

Builds every `UploadDirectory`, validates upload command, sizes, buffer size, booleans, cleanup rules, and extra rclone options. It tracks exact destination strings while parsing and rejects a repeated `remote_path` with an explicit safety warning before runtime state can collapse two destinations into one key.

It rejects application-reserved flags inside `copy_options`, including `--max-transfer`, `--cutoff-mode`, and `--buffer-size`. It also rejects `--delete-excluded` specifically for quota-managed `sync`, because over-budget files are intentionally absent from the generated exact upload list.

Why: users cannot accidentally recreate the transfer-limit failure, override dedicated runtime settings, delete intentionally skipped sync paths, or launch destructive workers concurrently for an identical destination.

### `load_config(config_path_text)`

Loads the complete config into `STATE`, including root `script_name` and a freshly parsed optional `mqtt` configuration, and validates thread limits, paths, and legacy schema rejection. It also fully parses `delete_min_age` before assigning runtime state, so `--validate-config` catches invalid age/duration values instead of deferring the error until cleanup planning.

It rejects obsolete top-level `directory_cleanup_rules`.

Why: all validation completes before execution begins, and cleanup rules are forced to remain attached to the remote they manage.

## `utils.py`

### `parse_size_to_bytes(size_text)`

Strictly accepts a whole number immediately followed by `K`, `KB`, `M`, `MB`, `G`, `GB`, `T`, or `TB` (case-insensitive after trimming), then converts it to binary bytes. Malformed placements, decimals, bare bytes, IEC spellings, and embedded spaces are rejected.

Why: all cleanup/reservation comparisons use integers, and a typo such as `G1` or `1G2` must fail closed instead of silently becoming a different size.

### `parse_duration_to_timedelta(duration_text)`

Parses rclone-style relative durations, including fixed `d`, `w`, `M`, and `y` suffixes plus Go-style `h/m/s` tokens.

Why: age planning must reproduce the configured rclone age semantics in Python.

### `parse_rclone_age_cutoff(time_text, now)`

Converts the configured minimum-age value into an absolute UTC cutoff, or returns `None` for `off`.

Why: snapshot `ModTime` values can be filtered without a separate remote `rclone delete --min-age` traversal.

### `validate_upload_command(command)`

Allows only `copy`, `sync`, or `move`.

Why: prevents arbitrary command names from being injected into the application-controlled rclone invocation.

### `remote_name_from_path(remote_path)`

Builds a filesystem-safe readable fragment, truncates that readable portion, and appends the full SHA-256 digest of the original `remote_path`.

Why: generated local filenames must avoid rclone path separators/colon syntax **and** distinct remote paths that sanitize to the same text (for example `a:b/c` and `a_b:c`) must not share a delete-list filename.

### `join_rclone_remote_path(remote_root, relative_path)`

Joins a remote root with one relative cleanup path.

Why: config remains relative while runtime cleanup targets are explicit full paths.

### `join_relative_path(base, child)`

Joins two normalized relative path fragments.

Why: avoids repeated slash-handling logic.

### `normalize_relative_path(path)`

Normalizes backslashes and strips leading/trailing slashes.

Why: path comparison/deduplication must use one canonical relative representation.

### `parse_rclone_modtime(modified)`

Parses rclone ISO modification time and requires timezone information, returning UTC.

Why: oldest-first ordering must be timezone-safe.

### `remote_file_oldest_sort_key(file)`

Returns `(UTC modification time, path)`.

Why: cleanup order is stable and deterministic when timestamps are equal.

### `format_bytes(size_bytes)`

Formats integer bytes as B/KiB/MiB/GiB/TiB.

Why: human-readable summaries should not change the integer values used by logic.

## `commands.py`

### `run_command(command, capture_output=False)`

Runs an argument list with `subprocess.run`, `text=True`, and never uses `shell=True`.

Why: external commands are executed without shell interpolation and can optionally return captured output for parsing/errors.

### `is_directory_not_found(result)`

Checks captured stdout/stderr for rclone's `directory not found` text.

Why: retained compatibility helper for callers that need to distinguish a missing remote directory from a different command failure.

## `rclone_backend.py`

### `get_rclone_config_dump()`

Runs `rclone config dump`, parses JSON, and caches it.

Why: backend type is required to select safe direct-delete flags, and repeated config reads are unnecessary.

### `resolve_underlying_backend_type(remote_path)`

Follows wrapper remotes such as `crypt`, `alias`, `chunker`, `compress`, and `hasher`, with loop/depth protection.

Why: a `crypt` destination still needs the delete option for its underlying Drive/MEGA/OneDrive backend.

### `get_delete_mode_options(target)`

Returns no extra option for trash mode. For direct mode it returns a known backend-specific hard-delete flag when supported.

Mappings:

- Drive: `--drive-use-trash=false`
- MEGA: `--mega-hard-delete`
- OneDrive: `--onedrive-hard-delete`

Why: direct deletion must be requested with the provider-specific rclone option rather than assumed.

### `get_delete_mode_text(target)`

Returns a human-readable description for startup summaries.

Why: destructive behavior should be visible before execution.

## `remote_files.py`

### `get_remote_file_entries(remote_path)`

Runs one recursive `rclone lsjson --recursive --files-only --no-mimetype`, parses the JSON array, validates every file's Path/Size/ModTime, normalizes paths, and returns `RemoteFile` objects.

Why: cleanup planning must fail closed on malformed snapshot data instead of deleting from incomplete/invalid metadata.

### `fetch_remote_snapshot(remote_path)`

Builds a `RemoteSnapshot` dictionary from one validated recursive listing.

Why: the snapshot becomes the single live input for a planning/verification phase.

### `clone_remote_snapshot(snapshot)`

Copies the path dictionary.

Why: planners can remove selected files from working state without mutating the original snapshot.

### `relative_cleanup_target_path(upload, target)`

Converts a full generated cleanup target back to a path relative to its owning upload root and verifies containment.

Why: prevents a cleanup target from being evaluated against a remote it does not belong to.

### `files_below_relative_path(snapshot, relative_path)`

Returns files exactly at or below one rule path.

Why: each cleanup rule only manages its intended subtree.

### `get_managed_snapshot_files(upload, snapshot)`

Returns the deduplicated union of files covered by all cleanup rules for an upload destination.

Why: overlapping cleanup rules must not double-count files for `max_total_size`.

### `get_upload_remote_quota_entries(upload)`

Compatibility helper that fetches a live snapshot and converts managed files to `RemoteQuotaFile` entries.

Why: preserves the older quota-helper interface without duplicating snapshot/filtering logic.

## `planning.py`

### `cleanup_targets_for_upload(cleanup_directories, upload)`

Filters generated targets to the upload that owns them.

Why: every worker should only plan deletions for its own remote.

### `plan_cleanup_targets(upload, targets, snapshot, plan, now=None)`

Applies age cleanup first, then `max_files`/`max_size` oldest-first limits to the mutable working snapshot.

Why: later rules see files already selected by earlier rules and do not re-select them.

### `plan_remote_quota_cleanup(upload, snapshot, plan)`

Applies upload-level `max_total_size` after rule cleanup by selecting oldest managed complete files until the managed size is within the limit.

Why: final quota enforcement should use the already-cleaned simulated state.

### `plan_upload_reservation(upload, snapshot, plan, selected_upload_bytes)`

Calculates temporary-space reservation from the bytes of the already-selected complete local files plus the 1 MiB safety headroom. It selects oldest complete managed remote files until the exact calculated deficit is covered. There is no one-byte transfer allowance and no rclone transfer cap.

Why: file selection now happens explicitly before reservation, so reservation needs only the byte total of the frozen selected set.

### `build_pre_upload_plan(upload, targets, snapshot, selected_upload_bytes)`

Clones the PRE-UPLOAD snapshot, runs cleanup-rule planning, then reservation planning for the selected local bytes, returning one combined plan plus reservation statistics.

Why: PRE-UPLOAD cleanup and reservation share one consistent remote state.

### `build_post_upload_plan(upload, targets, snapshot)`

Clones the POST-UPLOAD snapshot, runs cleanup-rule planning, then `max_total_size` enforcement.

Why: all post-upload deletion decisions are combined from one fresh live snapshot.

## `delete_plan.py`

### `add_planned_deletion(plan, snapshot, file, delete_to_trash, reason)`

Adds a file once and immediately removes it from the mutable working snapshot.

Why: duplicate selection is prevented and later rules see simulated post-delete state.

### `planned_delete_bytes(plan)`

Sums selected file sizes.

Why: prints an exact planned byte total before execution.

### `print_delete_plan_summary(job_number, plan)`

Prints file count, byte total, phase, and reason counts.

Why: destructive plans should be visible in logs.

### `execute_delete_plan(job_number, upload, plan, stage_name)`

Groups selected files by trash/direct mode, writes sorted `--files-from` lists, executes one rclone delete per required mode, and records failures. Delete-list filenames use the collision-resistant remote-name helper. After a trash-mode delete command succeeds, it records trash activity against the exact reservation/post-cleanup stage; a hard-delete command never sets that marker.

Why: preserves mixed delete modes while minimizing delete commands, prevents concurrent delete-list collisions, and lets later `rclone cleanup` run only for script-managed trash activity.

## `reservation.py`

### `clear_local_size_cache()`

Clears the per-run concurrent filtered-local-snapshot future cache.

Why: one run must not reuse stale file identities from an earlier invocation in the same process.

### `validate_local_upload_path(upload)`

Requires the local path to exist and be a directory.

Why: snapshotting/upload should fail before starting an invalid source command.

### `get_size_filter_options(upload)`

Extracts source-selection filters from `copy_options` for the local `rclone lsjson` snapshot. Supported selection flags include age/size filters, include/exclude/filter files, `--files-from*`, `--exclude-if-present`, `--hash-filter`, and `--ignore-case`.

Why: the local snapshot must represent the same candidate set the user configured without carrying unrelated transfer options such as stats or concurrency.

### `get_non_filter_copy_options(upload)`

Returns `copy_options` with source-selection flags removed.

Why: once quota planning has frozen the exact selected paths, the transfer uses generated `--files-from0`; rclone ignores other filters when `--files-from*` is present, so removing them keeps the command unambiguous while preserving non-filter runtime options.

### `local_size_cache_key(upload)`

Uses resolved local path plus source-selection filters as the cache key.

Why: multiple remotes uploading the same filtered source can share one exact scan safely.

### `_calculate_filtered_local_upload_snapshot(upload)`

Runs recursive local `rclone lsjson` with source filters and validates every returned `Path`, `Size`, and `ModTime`, rejecting duplicate paths. It returns a `LocalUploadSnapshot`.

Why: quota selection requires exact complete-file identities and modification times, not only aggregate `rclone size` output.

### `get_filtered_local_upload_snapshot(job_number, upload)`

Implements concurrent single-flight local snapshotting using a shared `Future`: one owner scans and matching workers wait for/reuse the exact result.

Why: GDrive/Mega/OneDrive-style uploads of the same source should not each rescan the local tree.

### `get_filtered_local_upload_size(job_number, upload)`

Compatibility helper that returns `(total_bytes, file_count)` from the cached exact local snapshot.

Why: existing internal callers/tests that only need aggregate sizing can reuse the same snapshot without another rclone command.

### `newest_first_local_files(files)`

Sorts filtered local candidates newest-first by rclone `ModTime`, with path as a deterministic tie-breaker.

Why: when the source exceeds the configured capacity, CCTV retention should prefer the newest footage.

### `select_local_upload_files(snapshot, max_upload_bytes)`

Walks candidates newest-first and selects a contiguous prefix of complete files. As soon as the next complete file would exceed the remaining budget, selection stops immediately; that file and every older file are deferred to a later run. The selector never searches farther into older files for a smaller fit.

Why: no partial file is intentionally transferred and a source larger than `max_total_size` becomes a successful capped upload rather than an rclone transfer-limit failure.

## `cleanup.py`

### `cleanup_one_trash_remote(job_number, upload, phase_name="")`

Runs optional `rclone cleanup` for one upload remote.

Behavior:

- `empty_trash=false` -> skip successfully.
- no tracked script-managed trash activity for the relevant barrier -> skip successfully, regardless of the upload-level default.
- pre-upload activity means a successful reservation-plan trash delete.
- post-upload activity means a successful post-cleanup-plan trash delete or an actually started trash-mode `sync`.
- backend reports cleanup unsupported -> skip successfully.
- other non-zero result -> record failure against reservation or post-cleanup stage depending on phase.

Why: trash emptying is optional and backend-dependent, and upload defaults or hard-delete overrides must not purge unrelated backend trash.

`cleanup.py` also re-exports `execute_delete_plan` so phase code can import both cleanup actions from one module.

## `upload.py`

### `print_thread_output(thread_number, remote_path, line)`

Serializes one streamed upload output line under the global output lock.

Why: concurrent rclone output remains readable and attributed to the correct remote.

### `run_command_streamed(command, thread_number, remote_path)`

Starts the upload process with merged stdout/stderr, streams lines live, captures them for later error reporting, and returns `(return_code, output)`.

Why: long uploads need live progress without losing failure text.

### `get_upload_buffer_options(upload)`

Returns `[]` or `['--buffer-size', configured_value]`.

Why: dedicated buffer config remains separate from free-form `copy_options`.

### `write_planned_upload_file_list(upload, planned_files)`

Writes the frozen quota-managed selection as a collision-resistant NUL-separated `to-upload-...files0` file below `delete_list_dir`.

Why: `--files-from0` safely carries exact paths including whitespace or comment-like prefixes and prevents newly-created files from entering after reservation.

### `upload_one_directory(job_number, upload)`

Validates the local source and upload command, adds sync delete-mode options when applicable, and for quota-managed uploads replaces source-selection filters with the generated exact `--files-from0` list while preserving non-filter options and configured buffer size. If quota planning selected zero files because the newest file already reached the cutoff, the upload stage succeeds as a no-op and starts no rclone upload command or upload-stage backend lookup. Otherwise it never adds `--max-transfer` or `--cutoff-mode`. Non-zero rclone status remains a real upload failure. Immediately before a trash-mode `sync` starts, it records that the upload stage may create script-managed trash, including partial work before a failure.

Why: the program, not rclone's transfer ceiling, decides the successful complete-file upload set; partial/error return codes are never relabeled as success.

## `phases.py`

### `print_snapshot_summary(job_number, snapshot_name, upload, snapshot)`

Prints recursive snapshot file count and total size.

Why: shows which live state each planning/verification stage used.

### `prepare_one_remote_for_upload(job_number, upload, cleanup_directories)`

Performs one remote's PRE-UPLOAD remote snapshot, optional exact filtered local snapshot, contiguous newest-first whole-file cap selection that stops at the first non-fitting file, cleanup/reservation planning, and combined delete execution. It stores the frozen selected paths for the later upload stage. It does **not** run trash cleanup or upload.

On failure it records reservation failure and skips that remote's upload.

Why: separating preparation from trash/upload makes a real global preparation barrier possible.

### `pre_upload_trash_cleanup_one_remote(job_number, upload)`

Runs the optional pre-upload trash cleanup after the preparation barrier, marks reservation success if it passes, then applies `sleep_after_step`.

Why: upload eligibility is only granted after the complete prerequisite reservation/trash stage succeeded.

### `run_reservation_and_upload_phase(cleanup_directories)`

Orchestrates three globally barriered stages:

1. PRE-UPLOAD preparation using `max(cleanup_threads, remote_quota_cleanup_threads)` workers.
2. PRE-UPLOAD trash cleanup using `trash_cleanup_threads` workers for prepared remotes only.
3. UPLOAD using `upload_threads` workers for remotes whose prerequisites succeeded.

It waits for all futures in each stage before constructing the next stage. Every future is handled individually; one failure sets the overall phase result to failed but does not cancel the other futures.

Why: this fixes cross-stage overlap while preserving per-remote failure isolation and safety skips.

### `post_cleanup_plan_one_remote(job_number, upload, cleanup_directories)`

Fetches the POST-UPLOAD snapshot, builds the combined cleanup/quota plan, and executes its planned deletes. It does not empty trash yet.

Why: all post-upload cleanup workers must finish before the post-upload trash stage begins.

### `post_upload_trash_cleanup_one_remote(job_number, upload)`

Runs optional post-upload trash cleanup and marks the post-cleanup stage successful.

Why: trash cleanup remains a separate barriered stage and only runs after successful planned deletion for that remote.

### `run_post_upload_cleanup_phase(cleanup_directories)`

Runs all post-upload cleanup/delete workers, waits for them all, then runs post-upload trash cleanup for eligible remotes and waits again.

Why: no remote can enter post-upload trash cleanup while another remote is still executing its post-upload cleanup plan.

### `verify_one_remote(job_number, upload, cleanup_directories)`

Fetches one FINAL snapshot and calls the shared final-limit verifier.

Why: final results must use fresh provider state after all cleanup/trash stages.

### `run_final_verification(cleanup_directories)`

Runs final verification for every remote with `max(cleanup_threads, remote_quota_cleanup_threads)` workers and waits for all of them.

Why: all remotes receive a final live safety check even when an earlier upload failed.

## `verification.py`

### `verify_upload_snapshot(job_number, upload, targets, snapshot)`

Checks every enabled cleanup-rule `max_files`/`max_size` limit and the upload's managed `max_total_size` from the same final snapshot.

Why: the program must not report success just because delete/upload commands returned zero; the final remote state must satisfy configured limits.

## `targets.py`

### `build_cleanup_directories()`

Converts every upload-owned relative cleanup rule into a full `CleanupTarget`, resolving nullable booleans from the upload-level defaults.

Why: inheritance is resolved once before worker execution, and every generated target retains its owner remote.

## `results.py`

### `initialize_run_results()`

Creates fresh `RemoteRunResult` objects for every configured upload destination.

Why: a new run must not inherit prior stage/error state.

### `get_stage_result(remote_path, stage_name)`

Returns the requested stage result object.

Why: centralizes access to per-remote result state.

### `record_stage_success(remote_path, stage_name)`

Marks success unless the stage has already failed.

Why: a later success must never erase an earlier failure.

### `record_stage_failure(remote_path, stage_name, error)`

Marks failure and appends deduplicated error text.

Why: all relevant failure causes should survive to the final report.

### `record_stage_skipped(remote_path, stage_name)`

Changes only a still-pending stage to `SKIPPED`.

Why: failed prerequisites should visibly prevent dependent work without overwriting existing status.

### `record_delete_plan_trash_deleted(remote_path, stage_name)`

Marks the reservation or post-cleanup trash-activity flag only after a trash-mode planned-delete command returned success. Unsupported stage names are rejected.

Why: later trash cleanup must be driven by what the script actually sent to backend trash, not merely by an inherited default.

### `record_upload_trash_mode_attempted(remote_path)`

Marks that an `rclone sync` using backend trash mode was actually started for the remote.

Why: sync can delete destination-only files before either succeeding or failing, so post-upload cleanup needs to remember that possible script-managed trash activity.

### `script_managed_trash_used(remote_path, stage_name)`

For the reservation barrier, returns whether a successful planned trash deletion occurred. For the post-cleanup barrier, returns whether either a successful post-cleanup trash deletion occurred or a trash-mode sync was actually started.

Why: `cleanup_one_trash_remote()` needs one concurrency-safe source of truth for whether `rclone cleanup` is justified.

### `finalize_stage_for_all(stage_name)`

Converts any still-pending stage to success.

Why: used after stages where a no-op path is still considered successful.

### `mark_pending_stages_skipped()`

At failed program completion, turns any unresolved stage into `SKIPPED`.

Why: the final report should never leave ambiguous `PENDING` statuses.

### `command_error_summary(output, fallback=...)`

Selects/deduplicates likely error lines from captured command output, falling back to the final lines when no obvious error keyword exists.

Why: final reports should be useful without dumping unlimited command logs.

### `remote_result_label(result)`

Returns `FAILED` if any stage failed, `SUCCESS` only if all four summary stages succeeded, otherwise `SKIPPED`.

Why: one concise remote-level result is derived consistently from the stage statuses.

### `print_final_run_result(exit_code)`

Prints the configured script name, every remote's stage statuses, retained error text, overall result, failed remote list, and exit code under output/result locks.

Why: concurrent work ends with one deterministic readable summary.

## `mqtt.py`

### `_stage_errors(result)`

Flattens retained stage errors for one remote into JSON objects containing `stage` and `error`.

Why: Home Assistant can inspect structured failures while the payload also retains human-readable text.

### `build_result_payload(exit_code)`

Builds schema version 1 of the final MQTT JSON object: script/application identity, UTC timestamp, success/failure state, exit code, combined failure text, failed friendly remote names, and every remote's stage statuses/errors.

Why: consumers such as Home Assistant receive one stable run-level event without having to parse console output.

### `_load_paho_mqtt()`

Imports `paho.mqtt.client` only when MQTT is actually enabled. Missing dependency becomes a clear runtime notification error.

Why: existing non-MQTT installations remain dependency-free and backward compatible.

### `_create_mqtt_client(mqtt, client_id)`

Creates a paho client using the 2.x callback API used by the optional pinned dependency range, with a compatibility fallback for older constructor shapes.

Why: optional MQTT support tolerates both major paho API generations without changing payload behavior.

### `publish_result_payload(payload)`

Applies configured authentication/TLS, connects to the broker, publishes compact JSON with configured QoS/retain, waits for completion, then disconnects/stops the network loop.

Why: the script does not report publication success merely because a payload was queued locally.

### `publish_final_result(exit_code)`

No-ops when MQTT is disabled. When enabled it builds/publishes the final payload and reports publication success or failure. Transport failure is intentionally caught and logged rather than changing the completed rclone run's exit code.

Why: MQTT is an optional observer/notification channel, not a prerequisite that can redefine whether the actual backup/cleanup work succeeded.

## `output.py`

### `print_step(message)`

Prints one high-level execution step.

Why: stage transitions/barriers are visible in logs.

### `print_error(message)`

Prints one error line to stderr.

Why: top-level failures are distinguishable from normal progress.

### `print_job_block(title, job_number, target, body)`

Uses `OUTPUT_LOCK` to print one complete worker block atomically.

Why: concurrent thread logs must not interleave line-by-line.

## `summary.py`

### `print_startup_summary(cleanup_directories)`

Prints script name, config path, barriered execution order, thread limits, reservation settings, optional MQTT settings without the password, each upload destination, each cleanup rule, and every resolved cleanup target.

Why: the operator can review effective destructive paths/settings before normal work starts; `--validate-config` uses the same summary without running rclone.

## `lock.py`

### `acquire_lock()`

Creates the configured lock atomically with `O_CREAT | O_EXCL`, writes the PID, and tracks whether this process owns it.

Why: prevents two normal instances from manipulating the same remotes/delete lists concurrently.

### `release_lock()`

Removes the lock only if this process recorded that it created it.

Why: a process must not delete a lock it does not own.

### `signal_handler(signum, frame)`

Prints the signal, releases the owned lock, and exits with `128 + signum`.

Why: handled termination should not leave a stale lock behind.

### `sleep_after_step()`

Legacy/shared helper that prints and sleeps for `STATE.sleep_after_step`.

Why: retained as a reusable configured-delay helper. The current barriered pre-upload flow performs its per-remote delay directly in `pre_upload_trash_cleanup_one_remote()` so each eligible trash worker reaches the upload barrier only after its delay.

## `main.py`

### `main()`

Top-level application orchestration:

1. parse CLI;
2. load/validate config;
3. build cleanup targets and initialize result tracking;
4. in validation mode, print summary and exit without lock/rclone;
5. otherwise register lock cleanup/signal handlers and acquire the lock;
6. print startup summary;
7. run barriered pre-upload preparation/trash/upload;
8. run barriered post-upload cleanup/trash;
9. run final verification;
10. aggregate overall status, mark unresolved stages skipped, and print final result;
11. optionally publish the schema-versioned MQTT result;
12. return the rclone/cleanup run exit code.

Why: only this module decides application-wide phase order and final process status; optional MQTT publication happens only after that status is finalized and does not redefine it.

# External rclone command map

## `rclone config dump`

Caller: `rclone_backend.get_rclone_config_dump()`.

Purpose: resolve the real backend behind wrappers so direct-delete flags are selected correctly.

## `rclone lsjson --recursive --files-only --no-mimetype REMOTE`

Caller: `remote_files.get_remote_file_entries()`.

Purpose: produce the PRE-UPLOAD, POST-UPLOAD, and FINAL live file snapshots.

## `rclone lsjson LOCAL --recursive --files-only --no-mimetype [filters]`

Caller: `reservation._calculate_filtered_local_upload_snapshot()`.

Purpose: return exact local candidate paths, sizes, and modification times selected by source filters for whole-file quota planning.

## `rclone delete --files-from DELETE_LIST REMOTE [delete-mode options]`

Caller: `delete_plan.execute_delete_plan()`.

Purpose: execute the combined oldest-first whole-file deletion plan.

Possible direct-delete options selected by backend:

```text
--drive-use-trash=false
--mega-hard-delete
--onedrive-hard-delete
```

## `rclone cleanup REMOTE`

Caller: `cleanup.cleanup_one_trash_remote()`.

Purpose: empty backend trash only for destinations configured for script-managed trash deletion plus `empty_trash=true`.

## `rclone copy LOCAL REMOTE [options]`

Caller: `upload.upload_one_directory()`.

Purpose: copy selected source files without deleting unrelated destination files as part of upload semantics. Quota-managed runs include generated `--files-from0 FILE` and do not use `--max-transfer`.

## `rclone sync LOCAL REMOTE [options]`

Caller: `upload.upload_one_directory()`.

Purpose: make the destination match the selected source according to rclone sync semantics. Delete-mode options are added when direct deletion is configured. Quota-managed sync rejects `--delete-excluded` because over-budget paths are intentionally excluded.

## `rclone move LOCAL REMOTE [options]`

Caller: `upload.upload_one_directory()`.

Purpose: transfer selected local files and allow rclone to remove successfully moved source files according to rclone move semantics. If quota planning selected zero files because the newest file already exceeded the available budget, the upload stage succeeds as a no-op and no transfer command is started.

# Safety-critical ordering summary

```text
ALL pre-upload preparation workers finish
        ↓
ALL eligible pre-upload trash workers finish
        ↓
ALL eligible upload workers finish
        ↓
ALL post-upload cleanup/delete workers finish
        ↓
ALL eligible post-upload trash workers finish
        ↓
ALL final verification workers finish
        ↓
FINAL RUN RESULT
```

Failure isolation applies within every worker pool. Prerequisite safety failures only block the affected remote from the dependent action; they do not cancel unrelated remotes.
