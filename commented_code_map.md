# Commented code map

This file documents the current application structure, every explicit application function/method/class, including nested validation helpers, the important shared state, and every external command the program can execute.

The central design rule is:

```text
workers inside one stage may finish independently
but the next stage does not begin until every worker in the current stage has finished
```

A failure is retained for the affected remote. Other remotes continue. A remote whose prerequisite safety stage failed is not allowed to proceed into the dependent upload/trash action.

## `rclone-multithreaded-upload.py`

Executable entry point calling supervised_main(main).

Why: retain the documented CLI filename while the supervisor owns the isolated job group, handles termination deadlines and detects death of either app PID. Help/version/validation retain direct behavior.

## `rclone_multithreaded_upload/__init__.py`

### `APPLICATION_NAME`, `VERSION`

Shared application identity and single version source. Reporting and live config reuse APPLICATION_NAME; CLI `--version` imports this value so the displayed version and packaged release version stay aligned.

## `models.py`

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

### `LocalUploadSnapshot.file_count()`

Read-only property returning the number of candidate files in the snapshot.

Why: callers can report candidate count without storing a second mutable count that could diverge from the exact file tuple.

### `RemoteFile`

Immutable representation of one recursive `rclone lsjson` file entry: path, size, modification time.

Why: cleanup/quota decisions can be calculated from one live snapshot instead of repeatedly listing the provider.

### `RemoteSnapshot`

In-memory mapping of overridden relative path to `RemoteFile` for one remote root.

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

### `StepRunResult`

Stores one planned operation's label, summary stage, kind, observation status, reason/error and UTC start/finish times. It never controls execution. A RemoteRunResult owns an insertion-ordered map of these objects so its denominator is fixed and its history is retained independently of parallel workers.

### `RemoteRunResult`

Contains detailed steps and an independent stderr tail as well as the four final summary groups for one remote: reservation, upload, post cleanup, final quota. It also retains three internal trash-activity markers: successful reservation-plan trash deletion, an actually started trash-mode upload `sync`, and successful post-cleanup-plan trash deletion.

Why: one failed remote can be reported without losing status for the other remotes, and later trash cleanup can distinguish script-managed trash activity from hard-delete-only work.

## `state.py`

### `RuntimeState`

Holds config-loaded runtime values and concurrency-safe shared dictionaries/locks.

Important fields include:

- `script_name`
- `mqtt` (completion config)
- `live_config` and `live_preview`
- `run_id`, `run_error`, `stderr_tail`, `comment`
- `reporting_lock`
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

### `reject_unknown_options(...)`

Compare supplied keys against the current allowed set and raise a section-qualified error for unsupported keys.

Why: Old settings and misspellings must fail before destructive work rather than silently disabling notification or cleanup settings.

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

### `optional_positive_int_or_none(section_name, config, key)`

Reads a positive integer or `null`.

Why: `max_files=0` would be a surprising destructive configuration, so only positive limits are accepted.

### `optional_string_list(section_name, config, key)`

Reads a list containing only strings.

Why: rclone option arrays must remain tokenized safely without shell parsing.

### Reporting config helpers

`_reporting_config()` rejects unknown options, resolves relative TLS files beside the JSON config and invokes the template's strict config validator. `parse_completion_config()` loads only current `completion` settings through the canonical MQTTConfig. `parse_live_config()` independently loads `live`, deriving a stable host/config device identity when no explicit device ID is supplied. See `MQTT.md` for current settings.

### `parse_cleanup_rules(section_name, raw_upload)`

Parses each upload-owned cleanup rule, normalizes its path, rejects duplicate overridden paths, validates optional size limits, and preserves nullable overrides.

Why: cleanup ownership and inheritance are resolved deterministically.

### `parse_upload_directories(config)`

Builds every `UploadDirectory`, validates upload command, sizes, buffer size, booleans, cleanup rules, and extra rclone options. It tracks exact destination strings while parsing and rejects a repeated `remote_path` with an explicit safety warning before runtime state can collapse two destinations into one key.

It rejects application-reserved flags inside `copy_options`, including `--max-transfer`, `--cutoff-mode`, and `--buffer-size`. It also rejects `--delete-excluded` specifically for quota-managed `sync`, because over-budget files are intentionally absent from the generated exact upload list.

Why: users cannot accidentally recreate the transfer-limit failure, override dedicated runtime settings, delete intentionally skipped sync paths, or launch destructive workers concurrently for an identical destination.

### `load_config(config_path_text)`

For live-enabled configs reject explicit log-file/syslog redirects before any lock/command, since they would remove the statistics stream from this reader.

Loads the complete config into `STATE`, including root `script_name`, `comment`, independent template `completion` / `live` settings, and validates thread limits, configured path strings, and strict rejection of unsupported keys at every config level. This is configuration validation, not a broker/remote connectivity or source-content check. It also fully parses `delete_min_age` before assigning runtime state, so `--validate-config` catches invalid age/duration values instead of deferring the error until cleanup planning.

It rejects obsolete top-level `directory_cleanup_rules`.

Why: all validation completes before execution begins, and cleanup rules are forced to remain attached to the remote they manage.

### `_reporting_config(...)`

Reject unknown MQTT options, resolve TLS file paths beside the config, and construct the template configuration class.

Why: Configuration mistakes must fail before runtime; certificate paths must not depend on the launch directory.

### `parse_completion_config(...)`

Parse only the current completion section with the canonical MQTTConfig and shared TLS-path/unknown-key validation.

Why: There is one supported reporting config format, with no legacy translation or alternate defaults.

### `parse_live_config(...)`

Load live settings separately, default manufacturer to the shared application name, and derive a stable host/config device ID when none is supplied.

Why: Enabling final notifications must not implicitly enable live monitoring or merge different configured jobs in HA.

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

Check the cancellation fence immediately before subprocess.run, so a worker cannot deliberately launch the next rclone command once a stop is requested. Command arguments and output capture stay unchanged.

Runs an argument list with `subprocess.run`, `text=True`, and never uses `shell=True`.

Why: external commands are executed without shell interpolation and can optionally return captured output for parsing/errors.

### `is_directory_not_found(result)`

Checks captured stdout/stderr for rclone's `directory not found` text.

Why: A remote directory that has not been created yet is an empty snapshot; other command failures must remain errors.

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

### `split_copy_options(...)`

Scan copy_options once and return ordered source-filter and transfer-only lists, retaining boolean flags, split values and equals-form options. Reject a missing source-filter value.

Why: Local selection and transfer commands must share one classification implementation without passing source filters alongside the frozen exact upload list.

### `clear_local_size_cache()`

Clears the per-run concurrent filtered-local-snapshot future cache.

Why: one run must not reuse stale file identities from an earlier invocation in the same process.

### `validate_local_upload_path(upload)`

Requires the local path to exist and be a directory.

Why: snapshotting/upload should fail before starting an invalid source command.

### `local_size_cache_key(upload)`

Uses resolved local path plus source-selection filters as the cache key.

Why: multiple remotes uploading the same filtered source can share one exact scan safely.

### `_calculate_filtered_local_upload_snapshot(upload)`

Runs recursive local `rclone lsjson` with source filters and validates every returned `Path`, `Size`, and `ModTime`, rejecting duplicate paths. It returns a `LocalUploadSnapshot`.

Why: quota selection requires exact complete-file identities and modification times, not only aggregate `rclone size` output.

### `get_filtered_local_upload_snapshot(job_number, upload)`

Implements concurrent single-flight local snapshotting using a shared `Future`: one owner scans and matching workers wait for/reuse the exact result.

Why: GDrive/Mega/OneDrive-style uploads of the same source should not each rescan the local tree.

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

Phase code imports execute_delete_plan directly from delete_plan.py; cleanup.py owns only the backend trash operation.

## `upload.py`

### `print_thread_output(thread_number, remote_path, line)`

Serializes one streamed upload output line under the global output lock.

Why: concurrent rclone output remains readable and attributed to the correct remote.

### `run_command_streamed(command, thread_number, remote_path)`

After an actual Popen launch, start the per-process meter, consume valid statistics lines without retaining them as failures, and always finish the meter/close stdout as the process reader unwinds. Preserve the original command return code and ordinary diagnostics.

Check the cancellation fence immediately before Popen; retain the original streamed output and return behavior. The CLI supervisor owns this subprocess group and stops it during cancellation.

Starts the upload process with merged stdout/stderr, streams lines live, captures them for later error reporting, and returns `(return_code, output)`.

Why: long uploads need live progress without losing failure text.

### `get_upload_buffer_options(upload)`

Returns `[]` or `['--buffer-size', configured_value]`.

Why: dedicated buffer config remains separate from free-form `copy_options`.

### `write_planned_upload_file_list(upload, planned_files)`

Writes the frozen quota-managed selection as a collision-resistant NUL-separated `to-upload-...files0` file below `delete_list_dir`.

Why: `--files-from0` safely carries exact paths including whitespace or comment-like prefixes and prevents newly-created files from entering after reservation.

### `upload_one_directory(job_number, upload)`

With live enabled, append explicit final values for rclone logging/progress/statistics display flags for structured one-second samples. Transfer options, delete modes, exact lists and quota decisions remain unchanged.

Validates the local source and upload command, adds sync delete-mode options when applicable, and for quota-managed uploads replaces source-selection filters with the generated exact `--files-from0` list while preserving non-filter options and configured buffer size. If quota planning selected zero files because the newest file already reached the cutoff, the upload stage succeeds as a no-op and starts no rclone upload command or upload-stage backend lookup. Otherwise it never adds `--max-transfer` or `--cutoff-mode`. Non-zero rclone status remains a real upload failure. Immediately before a trash-mode `sync` starts, it records that the upload stage may create script-managed trash, including partial work before a failure.

Why: the program, not rclone's transfer ceiling, decides the successful complete-file upload set; partial/error return codes are never relabeled as success.

## `phases.py`

### `run_remote_workers(uploads, workers, operation, *args, stage, label, error_prefix, skip_upload_on_error=False)`

Submit the ordered eligible destination list to StopAwareExecutor, join every future, and return its per-destination result map. A normal exception records the correct summary stage failure and error label; preparation/trash failures also skip their upload prerequisite when requested. KeyboardInterrupt/SystemExit propagate to the existing cancellation-aware executor.

Why: All six stage pools use the same failure isolation and joining code while phase callers retain their original worker limits, candidate selection and complete global barriers. No later stage starts until this helper returns.

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

Creates fresh `RemoteRunResult` objects for every configured upload destination and derives its fixed detailed-step plan using the existing effective cleanup-target builder.

Why: a new run must not inherit prior stage/error state.

### `get_stage_result(remote_path, stage_name)`

Returns the requested stage result object.

Why: centralizes access to per-remote result state.

### `record_stage_success(remote_path, stage_name)`

Marks success unless the stage has already failed.

Why: a later success must never erase an earlier failure.

### `record_stage_failure(remote_path, stage_name, error)`

Marks failure and appends deduplicated error text. After releasing the results lock, annotates any active detailed operation and skips unresolved observations only within that stage. This reflects the existing stage decision and does not add an eligibility decision.

Why: all relevant failure causes should survive to the final report.

### `record_stage_skipped(remote_path, stage_name)`

Changes only a still-pending stage to `SKIPPED` and settles its pending detailed observations with a prerequisite-skip reason.

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

`_stage_errors()` sanitizes and flattens stage failures. `build_result_payload()` calls the template's `build_report()` with rclone identity, authoritative integer exit code, diagnostic tail, warnings and run ID. Rclone fields remain as extensions: event, failed remotes, detailed remote results and progress. It emits no Syncerate dataset sections.

`publish_final_result()` applies the enabled gate and logs sanitized sent/skipped/failed delivery without replacing the job's code. Success filtering belongs to the completion config only.

### `_stage_errors(...)`

Flatten each remote's stage failures into sanitized stage/error objects.

Why: Structured remote diagnostics must survive without exposing known credential values.

### `build_result_payload(...)`

Snapshot per-remote results, aggregate errors, and call the existing template builder with the authoritative job code and rclone extensions.

Why: Completion fields stay template-compatible without duplicating the template serializer or losing rclone details. Standard fields remain authoritative; top-level progress and per-remote progress add the detailed final snapshot.

### `publish_final_result(...)`

Skip disabled completion reporting; otherwise build/send once and log sanitized sent/skipped/failed delivery.

Why: Notification failures must preserve the rclone job exit code and remain independent of HA live preview.

## `mqtt_status.py`

Unmodified supplied template 1.1.0: typed completion JSON validation, policy gates, non-retained Paho helper publishing, verified TLS, private worker routing and timeout handling. This module imports no live-preview module.

### `_text(...)`

Require a string, optionally with non-whitespace content, and return it unchanged.

Why: Typed identity and diagnostic fields must not silently accept numbers, booleans or empty names.

### `_json_text(...)`

Recursively validate JSON types/string keys and encode compact UTF-8 with NaN/infinity rejected.

Why: Both report payloads and subprocess requests must be valid interoperable JSON.

### `_json_text.check(...)`

Walk dictionary keys and list children and reject unsupported Python values.

Why: Python serialization must not silently coerce non-string keys or application-only objects into the report.

### `build_report(...)`

Build all standard typed completion fields, derive status from the integer code, add allowed extensions, and JSON-round-trip the report.

Why: Every adapter must follow one contract, preserve metadata and avoid mutable input references or core-field overrides.

### `encode_report(...)`

Validate required field types, schema version and status/success/code agreement, then serialize with _json_text.

Why: Mutated or manually created inconsistent reports must not be sent as valid completion events.

### `MQTTConfig.__post_init__(...)`

Validate switches, host/topic/port/QoS, finite positive timeout, authentication and paired TLS certificate options.

Why: Misconfigured notification settings must be rejected before a publisher can run.

### `publish_report(...)`

Apply disabled/success/dry-run gates, validate one payload snapshot, and run a deadline-bounded publisher via stdin with sanitized delivery results.

Why: Completion delivery must be optional, isolated and unable to replace the real job outcome or leak worker exceptions.

### `_publish_once(...)`

Lazily import the Paho single-message helper, build verified TLS/auth and send the encoded report with retain=false.

Why: A final completion event must use the configured broker policy and must not replay as retained running history.

### `run_worker_if_requested(...)`

Intercept only the exact private completion-worker flag, load and validate stdin, publish once and exit with worker delivery status.

Why: A frozen executable can safely re-execute itself as a publisher before argument parsing or destructive job work.

## `ha_live_preview.py`

Supplied template 1.2.0 with a local optional JSON-object details extension: independent manufacturer-aware config, local thread-safe progress, heartbeat, discovery, retained final outcome and killable MQTT subprocesses. This module imports no completion module. See `LIVE_PREVIEW.md` for the rclone measurement unit and every option.

### `_now(...)`

Return the current UTC time as an ISO timestamp.

Why: Every heartbeat and lifecycle timestamp needs an unambiguous timezone.

### `_json(...)`

Serialize compact UTF-8 JSON with non-finite numbers rejected.

Why: Snapshots and worker requests must remain valid MQTT JSON.

### `make_device_id(...)`

Normalize the job label and hash host, job and config identity into a stable device ID.

Why: Distinct hosts/configured jobs must not overwrite each other's HA discovery or live state.

### `LiveConfig.__post_init__(...)`

Validate booleans, MQTT addressing, QoS, TLS/auth fields, manufacturer text, device/topic identifiers and timing constraints.

Why: Invalid credentials/settings or an expiry shorter than the heartbeat budget must be caught before monitoring starts.

### `LiveConfig.state_topic(...)`

Build the non-retained state topic from topic_prefix and device_id.

Why: Live updates need an address separate from completion events and retained results.

### `LiveConfig.result_topic(...)`

Build the retained last_result topic for the live device.

Why: The last actual outcome must remain available after the expiring live sensors become unavailable.

### `discovery_messages(...)`

Construct retained HA discovery messages for 14 sensors, with the configured manufacturer, units/expiry, full status/result JSON attributes and stable live_preview_role tags. The manufacturer card uses the registry plus these tags to find fields automatically.

Why: HA must discover usable entities while treating ongoing state and retained results according to their different lifetimes.

### `LivePreview.__init__(...)`

Accept an optional callable before_publish callback; validate it and retain it as local state only. It is not an MQTT config option or serialized field.

Validate run identity and initialize locks, wake/stop events, process tracking, timestamps and unknown measurements.

Why: Each run needs isolated state and must not invent transfer totals or a successful outcome before work happens.

### `LivePreview._snapshot_locked(...)`

Build and JSON-detach state plus manufacturer/run metadata, current heartbeat and elapsed/frozen runtime while its lock is held.

Why: Concurrent job updates must not leak mutable references or produce inconsistent observations.

### `LivePreview.snapshot(...)`

Return a thread-safe local snapshot.

Why: Tests/adapters can inspect progress without network I/O or claiming broker delivery.

### `LivePreview.last_delivery(...)`

Read the synchronized local transport status.

Why: Delivery diagnostics must remain separate from the real job result.

### `LivePreview.start(...)`

Mark the run running and launch at most one daemon if enabled and permitted by dry-run policy.

Why: Disabled monitoring must not import Paho or start a publisher, and duplicate starts must not duplicate workers.

### `LivePreview.update(...)`

Validate and detach supplied progress fields and the optional JSON-object details, merge them atomically under lock, derive percentage/counts, and wake the daemon. Details carry application extensions without replacing standard fields.

Why: Job threads must update local memory quickly while keeping counters and typed JSON truthful.

### `LivePreview.append_stderr(...)`

Decode bytes when needed, retain a 4,000-character diagnostic tail, and wake the daemon.

Why: Diagnostics must remain bounded and tolerate imperfect UTF-8 without blocking real work.

### `LivePreview.finish(...)`

Finalize once with the real integer code, error/warning and interrupt/incomplete markers; freeze runtime and attempt a bounded final drain.

Why: A stopped or incomplete run must not be presented as success, and notification shutdown must have a time bound.

### `LivePreview._cancel(...)`

Set stop/wake events and kill any tracked MQTT subprocess.

Why: A stalled network worker must not hold the application open indefinitely.

### `LivePreview.close(...)`

Cancel monitoring and boundedly join its daemon without creating an outcome.

Why: Closing an unfinished monitor must leave the job outcome unknown rather than invent success.

### `LivePreview._send(...)`

Pass config/messages to a private publisher subprocess via stdin, enforce its timeout, and clean up the tracked process.

Why: DNS/network stalls and optional dependencies must stay isolated from job execution; credentials do not belong in process arguments.

### `LivePreview._worker(...)`

Immediately before snapshotting a batch, invoke before_publish outside the live lock; ignore callback errors so publishing/outcomes remain independent. This refreshes stalled speeds on heartbeats without another poller.

Coalesce changes, send heartbeats and periodic discovery, and drain final state/retained results with the proper retention policy.

Why: Frequent job updates should not flood the broker, while monitoring and final outcomes remain independently observable.

### `run_worker_if_requested(...)`

Intercept only the exact private live-worker invocation, validate stdin, lazily load Paho, create verified TLS and publish the message batch.

Why: The worker must never enter the job CLI or run uploads; ordinary invocations pass through unchanged.

## `progress.py`

Observational metadata only. Thread limits, eligibility, command arguments and barriers remain owned by the original application. CURRENT_REMOTE is a ContextVar, not one shared mutable current-worker name.

### `rule_step_id(...)`

Compose a stable key from pre/post planning phase and the effective owned rule path.

Why: The same cleanup path is observed separately before and after uploads without mixing destinations.

### `plan_steps(...)`

Build each destination's ordered plan from existing quota, rule, trash and delay settings.

Why: The overall denominator must be the sum of truthful per-destination plans, rather than assuming every destination has four identical tasks.

### `plan_steps.add(...)`

Add one typed observation with its label, summary stage and kind.

Why: The builder should initialize every step consistently without duplicate control logic.

### `set_step(...)`

Update one observation under the results lock, stamp real start/finish times, keep terminal observations immutable and refresh live data after unlocking.

Why: Concurrent workers must not overwrite each other or erase a failure; reporting errors must not replace a real job result.

### `step(...)`

Set the worker-local destination context, observe RUNNING/terminal state around real execution and re-raise the original BaseException unchanged.

Why: Captured diagnostics need correct ownership and failures/interrupts need truthful observation without changing execution behavior.

### `observe_step(...)`

Return a thin observer decorator for an existing operation, using its original argument signature and optional None-failure semantics.

Why: Reuse the original implementation and return values instead of introducing another upload/cleanup path.

### `observe_step.decorate(...)`

Capture the original operation signature and construct its wrapped observer.

Why: Positional/keyword upload arguments must resolve to the same destination for every existing caller.

### `observe_step.decorate.observed(...)`

Bind original arguments, enter the step context, call the original function exactly once and observe False/optional-None results before returning unchanged.

Why: Monitoring must accurately represent operations while preserving their actual result or exception.

### `skip_step(...)`

Record a terminal skipped observation with its actual no-op/prerequisite reason.

Why: A successful no-op should not be falsely presented as a file transfer or deletion.

### `settle_stage(...)`

Mirror an existing failed/skipped stage by marking active observations and skipping remaining ones in that stage only. Errors outside an observed operation stay on the destination/stage.

Why: Do not fabricate execution of an unstarted step, skip allowed post-cleanup, or cancel another destination.

### `finish_steps(...)`

Settle any unresolved active/pending observations after the job ends, preserving interruption and using skipped for unreached work.

Why: Final details must not leave ghost running workers or invent successful work.

### `remote_progress(...)`

Merge transfer_snapshot fields for that destination, including measured bytes/rate, upload duration/average, sample availability and stale-speed state.

Also derive activity labels/counts from this destination's RUNNING observations. Trash IDs get their own category while retaining the original cleanup kind. Idle and finished rows expose no stale active operation.

Why: The overview must show which part each worker is doing, without changing worker scheduling.

Snapshot a destination's complete ordered step table, counts/current operations, per-cleanup-path pre/post counters, measured transfer fields, diagnostics and error flags while the caller owns the results lock.

Why: Each destination needs its own consistent view, independent of the last reporting worker.

### `progress_snapshot_locked(...)`

Sum transferred bytes and current rates across the same locked destination snapshots. Retain null for an unknown launched-worker metric, and zero for finished/unlaunched current speed.

Also sum activity counts across the same destination snapshot and build the top readable activity summary. Upload, ordinary cleanup and trash cleanup remain distinguishable when different destinations are active.

Why: The run overview must represent every active destination rather than whichever thread last updated a field.

Build all destination snapshots once and sum their fixed totals, settled/running/failure/skip counts and measured bytes/rates. No duplicate four-stage progress counters are emitted.

Why: One atomic details object must agree with the whole-run counters and report every active worker.

## `reporting.py`

`start_reporting()` creates a fresh run ID and starts an enabled live monitor after the caller owns the lock. `update_live()` applies global phase/current-remote updates. `refresh_live()` snapshots detailed steps and stage outcomes under locks, counts settled steps and maps local/remote jobs to the template's outcome arrays. The adapter lock orders concurrent snapshots and updates to prevent progress moving backward.

`append_diagnostics()` keeps a shared sanitized 4,000-character diagnostic tail for both channels. `sanitize()` removes configured MQTT passwords and common credential flags. `finish_live()` refreshes outcomes, finalizes with the real code/error/warnings/interrupt marker and closes the monitor.

Stage recording helpers in `results.py` call `refresh_live()` after releasing the result lock. Captured-command helpers append stderr when commands return; the existing merged upload stream appends each output line and updates the current remote.

### `sanitize(...)`

Redact configured MQTT passwords and common secret-bearing rclone flags from report diagnostics.

Why: Forwarding command errors to HA should not unnecessarily expose known credentials.

### `append_diagnostics(...)`

Sanitize and append captured output to the shared bounded stderr tail, the current worker destination's independent tail via CURRENT_REMOTE, and the enabled live monitor. Refresh detached details so the card sees that destination's output.

Why: Both channels should show consistent bounded diagnostics; a live update exception must not fail the job.

### `start_reporting(...)`

Reset report state, assign one new run ID, initialize detailed totals in the enabled monitor before its first publisher batch, and start it only after the caller owns the job lock.

Why: Both channels need the same job identity and must not start duplicate reporting for an existing-lock skip.

### `update_live(...)`

Forward global phase/current-item fields to the optional local monitor with non-fatal error handling.

Why: Progress observations must never become a prerequisite for upload/cleanup work.

### `refresh_live(...)`

Update the standard transferred_bytes/bytes_per_second atomically with counters and detailed per-destination observations. The live publisher invokes this local refresh before each packet/heartbeat.

Serialize concurrent snapshots under adapter/result locks, use progress_snapshot_locked for all destination counters/current operations, and map authoritative stage outcomes to live result lists. The keyword-only clear_current flag clears the summary at finish; ignored positional labels are not supported.

Why: Counters must not move backward due to racing workers; only actual outcomes should classify success, failure or skipped.

### `finish_live(...)`

Refresh final progress, finalize with the real code/error/warning/interrupt fields and close the optional monitor.

Why: HA should receive the true final result and bounded shutdown while completion reporting remains independent.

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

Set cooperative cancellation without printing from a signal handler, notify the outer owner even when htop signals the inner job directly, and exit with 128+signal on the first request. Repeated inner signals do not raise another exception during finalization.

Prints the signal and raises `SystemExit(128 + signum)`. Main finalizes enabled reporting channels before releasing the owned lock.

Why: handled termination should not leave a stale lock behind.

## `main.py`

### `main()`

Reset per-invocation cancellation, register SIGINT/SIGTERM/SIGHUP/SIGQUIT handling, preserve signal exit codes and interrupted outcomes, and clear cancellation after final reporting/lock release. The CLI entry point calls this inside supervised_main; direct library calls do not install the outer supervisor.

Top-level application orchestration:

1. parse CLI;
2. load/validate config;
3. build cleanup targets and initialize result tracking;
4. in validation mode, print summary and exit without lock/rclone;
5. otherwise register lock cleanup/signal handlers and acquire the lock;
6. start enabled live monitoring, assign a shared run ID and print startup summary;
7. run barriered pre-upload preparation/trash/upload;
8. run barriered post-upload cleanup/trash;
9. run final verification;
10. aggregate overall status, mark unresolved stages skipped, settle unobserved detailed steps honestly, and print final result;
11. finalize enabled live preview and independently publish the template completion result;
12. release the owned lock and return the rclone/cleanup run exit code.

Private completion/live worker hooks run before the normal CLI parser. Runtime exceptions and handled interrupts enter the same final reporting boundary with a failure code. Validation and existing-lock exits never start reporting.

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

# Private MQTT subprocess commands

These are implementation routes, not public CLI options. The main entry point checks both hooks before parsing public arguments; a normal user run never supplies these flags manually.

| Command | What it does | Why |
| --- | --- | --- |
| `python3 -B <package>/mqtt_status.py --mqtt-status-worker` | Read one completion request from stdin and invoke Paho single-message publishing. | Isolate DNS/connect/publish and enforce the parent deadline without running a job. |
| `python3 -B <package>/ha_live_preview.py --ha-live-preview-worker` | Read one live message batch from stdin and invoke Paho multi-message publishing. | Keep network work outside job threads and allow cancellation. |
| `<frozen executable> --mqtt-status-worker` | Route a frozen executable to its completion hook. | Reuse the executable when no standalone Python worker file exists. |
| `<frozen executable> --ha-live-preview-worker` | Route a frozen executable to its live hook. | Preserve the same process isolation for frozen builds. |

`-B` suppresses worker bytecode writes. Authentication travels in JSON on stdin, not in arguments. Worker transport exit codes describe delivery; the adapter preserves the real rclone job code. This project does not supply or require a frozen build.

# Offline test function inventory

Tests exercise the same application/template functions with fake rclone and Paho helpers. Test fixtures may create files only in temporary test directories. Test function names below describe the guarded behavior; assertions exist to prevent regressions in that named contract. Nested helpers provide deterministic failure/concurrency/transport simulations, not production alternatives.

## `tests/test_ha_live_preview.py`

### `wait_for(...)`

Poll a test predicate until it succeeds or a short deadline expires.

Why: Background-worker tests must be bounded without assuming exact thread timing.

### `LiveTests.live(...)`

Build a live-preview instance with stable test identity and supplied settings.

Why: Lifecycle tests should reuse the real monitor class.

### `LiveTests.test_identity_is_stable_and_separates_configs_and_hosts(...)`

Check identity is stable and separates configs and hosts.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_config_validation_and_secret_repr(...)`

Check config validation and secret repr.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_disabled_module_maintains_local_progress_without_processes(...)`

Check disabled module maintains local progress without processes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_progress_is_derived_and_unknown_is_null(...)`

Check progress is derived and unknown is null.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_rejects_bad_counters_and_direct_status_updates(...)`

Check rejects bad counters and direct status updates.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_stderr_tail_and_utf8_replacement(...)`

Check stderr tail and utf8 replacement.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_completed_success_has_typed_code_and_shared_identity(...)`

Check completed success has typed code and shared identity.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_failure_and_interrupt_do_not_force_progress_to_100(...)`

Check failure and interrupt do not force progress to 100.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_zero_code_with_remaining_work_is_incomplete(...)`

Check zero code with remaining work is incomplete.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_finish_is_idempotent_freezes_runtime_and_rejects_late_updates(...)`

Check finish is idempotent freezes runtime and rejects late updates.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_close_without_finish_does_not_invent_a_terminal_result(...)`

Check close without finish does not invent a terminal result.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_discovery_separates_expiring_state_and_retained_result(...)`

Check discovery separates expiring state and retained result.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_live_has_dataset_lists_and_counts(...)`

Check live has dataset lists and counts.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_bulk_dataset_reclassification_replaces_counts_and_keeps_reasons(...)`

Replace live outcome groups through the bulk update API; counts must match current lists and skipped reasons must survive.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_dataset_lists_and_snapshots_are_detached(...)`

Check dataset lists and snapshots are detached.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_failed_datasets_prevent_zero_code_live_success(...)`

Check failed datasets prevent zero code live success.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_dataset_input_validation_and_no_updates_after_finish(...)`

Check dataset input validation and no updates after finish.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_background_heartbeat_and_terminal_batch(...)`

Check background heartbeat and terminal batch.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_background_heartbeat_and_terminal_batch.send(...)`

Provide the nested test helper to capture outgoing messages without a broker.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LiveTests.test_updates_are_local_and_coalesced_while_network_is_slow(...)`

Check updates are local and coalesced while network is slow.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_updates_are_local_and_coalesced_while_network_is_slow.slow(...)`

Provide the nested test helper to simulate a slow broker operation.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LiveTests.test_disabled_dry_run_does_not_start_worker(...)`

Check disabled dry run does not start worker.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_opted_in_dry_run_never_overwrites_retained_last_result(...)`

Check opted in dry run never overwrites retained last result.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_actual_worker_isolation_and_final_payload_with_fake_paho(...)`

Check actual worker isolation and final payload with fake paho.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_real_stalled_worker_is_killed_with_bounded_final_wait(...)`

Check real stalled worker is killed with bounded final wait.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_failed_delivery_does_not_change_job_outcome(...)`

Check failed delivery does not change job outcome.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_frozen_launch_and_tls_worker_hook(...)`

Check frozen launch and tls worker hook.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_module_imports_without_completion_module_or_paho(...)`

Check module imports without completion module or paho.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LiveTests.test_manufacturer_is_configurable_in_discovery_and_state(...)`

Check manufacturer is configurable in discovery and state.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `LiveTests.test_invalid_manufacturer_names_are_rejected(...)`

Check invalid manufacturer names are rejected.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `LiveTests.test_aggregate_sensors_have_stable_role_markers(...)`

Check aggregate sensors have stable role markers.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

## `tests/test_integration_fake_rclone.py`

### `FakeRcloneIntegrationTests.test_complete_application_flow_and_stage_barriers(...)`

Check complete application flow and stage barriers.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `FakeRcloneIntegrationTests.test_over_budget_upload_stops_at_first_non_fitting_file_without_transfer_cap(...)`

Check over budget upload stops at first non fitting file without transfer cap.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `FakeRcloneIntegrationTests.test_partial_upload_failure_still_reports_completion_and_live_result(...)`

Check partial upload failure still reports completion and live result.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `FakeRcloneIntegrationTests._run_complete_application_flow(...)`

Run the real entry script with fake rclone and real publisher workers/fake Paho, parameterized for success or partial-upload failure.

Why: Reuse the original full-flow assertions while proving both outcome JSON contracts, manufacturer/role discovery and safety barriers.

## `tests/test_logic.py`

### `ConfigValidationTests.test_unknown_options_and_removed_sections_are_rejected_before_work(...)`

Run the actual validation CLI with obsolete root sections and unknown root/thread/upload/cleanup keys; require a nonzero exit and a precise unknown-option error.

Why: Removing conversion code must fail closed instead of silently accepting unsafe or disabled settings.

### `StateSnapshot.__enter__(...)`

Snapshot shared runtime state before a test.

Why: Independent tests must not inherit mutated config or results.

### `StateSnapshot.__exit__(...)`

Clear the local snapshot cache and restore saved runtime state.

Why: Threaded tests must not contaminate later tests.

### `CliHelpTests.run_cli(...)`

Invoke the actual entry script with captured output and a timeout.

Why: Help and parser behavior must be verified at the real executable boundary.

### `CliHelpTests.assert_complete_flag_help(...)`

Assert the help text describes every public flag.

Why: Parser errors and --help must remain equally informative.

### `CliHelpTests.test_help_lists_every_cli_flag_and_explanation(...)`

Check help lists every cli flag and explanation.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `CliHelpTests.test_unknown_flag_prints_complete_help_before_error(...)`

Check unknown flag prints complete help before error.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `CliHelpTests.test_missing_required_config_prints_complete_help_before_error(...)`

Check missing required config prints complete help before error.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.run_validate_config(...)`

Write a temporary JSON config and invoke the actual validation CLI.

Why: Configuration tests must prove validation refuses bad input before work starts.

### `ConfigValidationTests.minimal_upload(...)`

Construct a minimal valid upload entry for one test.

Why: Each validation test should isolate the setting being tested.

### `ConfigValidationTests.test_validate_config_rejects_invalid_delete_min_age(...)`

Check validate config rejects invalid delete min age.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_validate_config_rejects_malformed_size(...)`

Check validate config rejects malformed size.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_duplicate_upload_destination_warns_and_refuses_to_continue(...)`

Check duplicate upload destination warns and refuses to continue.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_quota_managed_sync_rejects_delete_excluded(...)`

Check quota managed sync rejects delete excluded.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_script_name_and_mqtt_settings_validate_and_hide_password(...)`

Check script name and mqtt settings validate and hide password.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_mqtt_enabled_requires_host(...)`

Check mqtt enabled requires host.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_mqtt_qos_is_validated(...)`

Check mqtt qos is validated.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigValidationTests.test_script_name_and_publish_topic_reject_null_or_wildcards(...)`

Check script name and publish topic reject null or wildcards.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `MqttResultTests._one_upload(...)`

Construct one named remote upload fixture.

Why: Completion mapping tests need a consistent real model.

### `MqttResultTests._initialize_results(...)`

Install the upload fixture and initialize real stage results.

Why: Payload tests must use the production accounting code.

### `MqttResultTests.test_success_payload_is_home_assistant_friendly(...)`

Check success payload is home assistant friendly.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `MqttResultTests.test_failure_payload_includes_remote_and_error_text(...)`

Check failure payload includes remote and error text.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `MqttResultTests.test_disabled_mqtt_does_not_load_optional_dependency(...)`

Check disabled mqtt does not load optional dependency.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `MqttResultTests.test_publish_failure_is_reported_but_does_not_raise(...)`

Check publish failure is reported but does not raise.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_parse_size_and_duration_helpers(...)`

Check parse size and duration helpers.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_delete_list_remote_names_are_collision_resistant(...)`

Check delete list remote names are collision resistant.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_copy_options_partition_preserves_filters_and_transfer_options(...)`

Check size filter options only copy source selection filters.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_managed_snapshot_union_deduplicates_overlapping_rules(...)`

Check managed snapshot union deduplicates overlapping rules.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_pre_plan_reuses_one_snapshot_for_age_limits_and_reservation(...)`

Check pre plan reuses one snapshot for age limits and reservation.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_local_size_single_flight_scans_identical_source_once(...)`

Check local size single flight scans identical source once.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_combined_delete_plan_uses_one_delete_for_one_mode(...)`

Check combined delete plan uses one delete for one mode.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_mixed_delete_plan_preserves_modes_and_enables_trash_cleanup(...)`

Check mixed delete plan preserves modes and enables trash cleanup.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_mixed_delete_plan_preserves_modes_and_enables_trash_cleanup.fake_delete_options(...)`

Provide the nested test helper to return deterministic backend flags.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_hard_delete_only_plan_never_empties_trash_even_if_upload_default_is_trash(...)`

Check hard delete only plan never empties trash even if upload default is trash.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_trash_mode_sync_attempt_is_tracked_for_post_upload_cleanup(...)`

Check trash mode sync attempt is tracked for post upload cleanup.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_quota_managed_upload_uses_exact_file_list_without_max_transfer(...)`

Check quota managed upload uses exact file list without max transfer.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_complete_file_selection_below_equal_and_above_budget(...)`

Check complete file selection below equal and above budget.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_complete_file_selection_stops_at_first_non_fitting_file(...)`

Check complete file selection stops at first non fitting file.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_complete_file_selection_stops_immediately_if_newest_file_does_not_fit(...)`

Check complete file selection stops immediately if newest file does not fit.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_empty_quota_selection_is_successful_noop_without_rclone(...)`

Check empty quota selection is successful noop without rclone.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_planned_partial_upload_error_is_still_failure_not_success(...)`

Check planned partial upload error is still failure not success.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_stage_barriers_wait_for_all_preparation_and_trash_jobs(...)`

Check stage barriers wait for all preparation and trash jobs.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_stage_barriers_wait_for_all_preparation_and_trash_jobs.fake_snapshot(...)`

Provide the nested test helper to simulate remote listings and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_stage_barriers_wait_for_all_preparation_and_trash_jobs.fake_trash(...)`

Provide the nested test helper to simulate backend trash cleanup and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_stage_barriers_wait_for_all_preparation_and_trash_jobs.fake_upload(...)`

Provide the nested test helper to simulate upload success/failure and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_failed_preparation_does_not_cancel_other_uploads(...)`

Check failed preparation does not cancel other uploads.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_failed_preparation_does_not_cancel_other_uploads.fake_snapshot(...)`

Provide the nested test helper to simulate remote listings and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_failed_preparation_does_not_cancel_other_uploads.fake_upload(...)`

Provide the nested test helper to simulate upload success/failure and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_upload_failure_does_not_cancel_other_upload_workers(...)`

Check upload failure does not cancel other upload workers.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_upload_failure_does_not_cancel_other_upload_workers.fake_upload(...)`

Provide the nested test helper to simulate upload success/failure and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_post_cleanup_barrier_waits_before_trash_cleanup(...)`

Check post cleanup barrier waits before trash cleanup.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_post_cleanup_barrier_waits_before_trash_cleanup.fake_snapshot(...)`

Provide the nested test helper to simulate remote listings and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_post_cleanup_barrier_waits_before_trash_cleanup.fake_trash(...)`

Provide the nested test helper to simulate backend trash cleanup and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_post_cleanup_failure_does_not_cancel_other_remotes(...)`

Check post cleanup failure does not cancel other remotes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_post_cleanup_failure_does_not_cancel_other_remotes.fake_snapshot(...)`

Provide the nested test helper to simulate remote listings and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_post_cleanup_failure_does_not_cancel_other_remotes.fake_trash(...)`

Provide the nested test helper to simulate backend trash cleanup and timing.

Why: Make the parent regression scenario deterministic without real cloud or broker operations.

### `LogicTests.test_packaged_example_config_loads_expected_runtime_state(...)`

Check packaged example config loads expected runtime state.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LogicTests.test_local_size_single_flight_scans_identical_source_once.fake_calculate(...)`

Simulate a slow counted local snapshot calculation.

Why: The single-flight cache must run one calculation for identical concurrent sources.

### `LogicTests.test_quota_managed_upload_uses_exact_file_list_without_max_transfer.fake_stream(...)`

Capture the generated exact-file upload command and list.

Why: Quota-selected uploads must reuse their frozen file list without transfer-cap flags.

### `StageWorkerTests.test_all_stage_crashes_still_isolate_destinations_and_skip_only_dependents(...)`

Run all six stage crash cases with two destinations. Verify the failed destination receives the correct stage failure, its upload prerequisites are skipped where required, and the independent destination still runs.

Why: Preserve stage failure isolation, barrier behavior and shutdown fencing while removing repeated worker-pool code.

### `StageWorkerTests.test_all_stage_crashes_still_isolate_destinations_and_skip_only_dependents.perform(...)`

Record job numbering and destination identity, fail only destination A, and let B complete so the actual phase runner must isolate the exception.

Why: Preserve stage failure isolation, barrier behavior and shutdown fencing while removing repeated worker-pool code.

### `StageWorkerTests.test_shared_runner_propagates_cancellation_and_accepts_empty_barriers(...)`

Verify an empty eligible list launches no operation, KeyboardInterrupt is propagated instead of recorded as an ordinary worker failure, and the stop-aware executor sets cancellation. Always reset the cancellation event for other tests.

Why: Preserve stage failure isolation, barrier behavior and shutdown fencing while removing repeated worker-pool code.

## `tests/test_mqtt_status.py`

### `ReportTests.report(...)`

Call the real template report builder with stable default identity.

Why: Contract tests should differ only in the fields under test.

### `ReportTests.test_syncerate_core_fields_are_present_and_typed(...)`

Check syncerate core fields are present and typed.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_generic_completion_report_contains_no_dataset_sections(...)`

Check generic completion report contains no dataset sections.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_explicit_syncerate_diagnostics_remain_supported_including_empty_lists(...)`

Check explicit syncerate diagnostics remain supported including empty lists.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_invalid_optional_dataset_extensions_are_rejected(...)`

Check invalid optional dataset extensions are rejected.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_failures_preserve_nonzero_and_signal_codes(...)`

Check failures preserve nonzero and signal codes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_rejects_boolean_or_string_exit_codes(...)`

Check rejects boolean or string exit codes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_warning_and_preview_are_explicit_and_do_not_change_outcome(...)`

Check warning and preview are explicit and do not change outcome.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_bounded_unicode_stderr_and_null_normalization(...)`

Check bounded unicode stderr and null normalization.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_extra_project_details_and_datasets_are_detached(...)`

Check extra project details and datasets are detached.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_extensions_cannot_override_contract(...)`

Check extensions cannot override contract.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_rejects_non_json_extensions(...)`

Check rejects non json extensions.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ReportTests.test_encoder_rejects_mutated_inconsistent_reports(...)`

Check encoder rejects mutated inconsistent reports.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.setUp(...)`

Construct a valid enabled completion configuration.

Why: Transport tests need a known policy and broker fixture.

### `TransportTests.test_config_rejects_invalid_values(...)`

Check config rejects invalid values.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_disabled_mqtt_does_no_work_even_without_a_valid_report(...)`

Check disabled mqtt does no work even without a valid report.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_on_success_false_suppresses_only_success(...)`

Check on success false suppresses only success.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_preview_suppression_is_independent(...)`

Check preview suppression is independent.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_credentials_use_stdin_and_parent_sets_timeout(...)`

Check credentials use stdin and parent sets timeout.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_timeout_and_worker_failures_are_nonfatal_and_sanitized(...)`

Check timeout and worker failures are nonfatal and sanitized.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_invalid_report_does_not_launch_a_worker(...)`

Check invalid report does not launch a worker.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_frozen_worker_reexecutes_binary(...)`

Check frozen worker reexecutes binary.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_actual_subprocess_worker_and_timeout_with_fake_paho(...)`

Check actual subprocess worker and timeout with fake paho.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_tls_verifies_certificates_and_loads_client_pair(...)`

Check tls verifies certificates and loads client pair.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_import_does_not_require_paho_or_launch_processes(...)`

Check import does not require paho or launch processes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_normal_worker_hook_does_not_consume_application_arguments(...)`

Check normal worker hook does not consume application arguments.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `TransportTests.test_source_syntax_is_python_310_compatible(...)`

Check source syntax is python 310 compatible.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

## `tests/test_reporting_adapter.py`

### `ConfigTests.test_combined_template_configuration_and_disabled_defaults(...)`

Check combined template configuration and disabled defaults.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigTests.test_tls_paths_resolve_beside_config_and_password_repr_is_private(...)`

Check tls paths resolve beside config and password repr is private.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigTests.test_default_live_identity_is_stable_and_separates_configs(...)`

Check default live identity is stable and separates configs.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigTests.test_current_completion_config_uses_template_defaults_and_timeout(...)`

Check current template defaults and explicit publish timeout.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigTests.test_removed_completion_keys_and_unknown_options_are_rejected(...)`

Check obsolete completion keys and unknown options are rejected.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.run_job(...)`

Run actual main/config/lock/finalization with temporary paths and fake phase/transport boundaries.

Why: Adapter tests must prove channel independence and exit-code/lock ownership across normal and failure paths.

### `LifecycleTests.test_all_four_channel_combinations_are_independent(...)`

Check all four channel combinations are independent.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.test_stage_failures_and_exceptions_report_both_channels(...)`

Check stage failures and exceptions report both channels.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.test_interrupts_finalize_before_releasing_the_lock(...)`

Check interrupts finalize before releasing the lock.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.test_delivery_failure_preserves_success_and_failure_codes(...)`

Check delivery failure preserves success and failure codes.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.test_validation_and_existing_lock_do_not_start_channels(...)`

Check validation and existing lock do not start channels.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `LifecycleTests.test_live_update_failure_does_not_change_job_result(...)`

Check live update failure does not change job result.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ProgressTests.test_concurrent_stage_counters_are_consistent_and_monotonic(...)`

Check concurrent stage counters are consistent and monotonic.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ProgressTests.test_diagnostics_are_bounded_and_redacted_in_both_channels(...)`

Check diagnostics are bounded and redacted in both channels.

Why: Detect regressions in this behavior using offline assertions against the actual implementation.

### `ConfigTests.test_rclone_manufacturer_defaults_for_existing_configs_and_can_be_overridden(...)`

Check rclone manufacturer defaults for existing configs and can be overridden.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `LifecycleTests.run_job.reservation(...)`

Simulate real stage recording or inject an exception/interrupt while holding the job lock.

Why: Lifecycle tests must exercise both success and abnormal final reporting.

### `LifecycleTests.run_job.cleanup(...)`

Record a successful post-cleanup stage.

Why: The fixture must use production accounting when progressing the job.

### `LifecycleTests.run_job.verify(...)`

Record a successful final-quota stage.

Why: Completion and live counters must reflect all four real stage fields.

### `LifecycleTests.run_job.send(...)`

Capture background live message batches without network I/O.

Why: Verify channel independence and state/discovery/result retention safely.

### `LifecycleTests.run_job.publish(...)`

Validate/capture completion JSON and check that the lock is still held.

Why: Final notifications must preserve lock ownership and the real job outcome.

### `ProgressTests.test_concurrent_stage_counters_are_consistent_and_monotonic.update(...)`

Record stage counters before calling the actual local monitor update.

Why: Concurrent progress snapshots must not move backward.

## `tests/test_manufacturer_card.py`

Offline regression checks using the actual application/template functions.

### `FakeHA.__init__(...)`

Initialize an in-memory entity and device registry.

Why: Card tests need deterministic HA registry data without connecting to HA.

### `FakeHA.sensor(...)`

Return the registered sensor objects.

Why: The card scans states.sensor to discover all matching jobs automatically.

### `FakeHA.__call__(...)`

Return an entity state, defaulting to unknown.

Why: Simulate the HA states(entity_id) template helper.

### `FakeHA.state_attr(...)`

Read one registered entity attribute or return None.

Why: Card fields must come from the actual discovered status/result attributes.

### `FakeHA.device_id(...)`

Resolve an entity to its simulated device.

Why: Manufacturer grouping works through device membership rather than hardcoded entity IDs.

### `FakeHA.device_attr(...)`

Resolve a device/entity and read its device-registry attribute.

Why: The card must filter by manufacturer and display the device name.

### `FakeHA.device_entities(...)`

List entities attached to a device.

Why: Role-marker discovery must continue working when entities are renamed.

### `CardTests.setUp(...)`

Create the strict HA/Jinja test environment and fake registry, then load the actual template content from manufacturer-live-preview-card.yaml.

Why: Card tests must exercise the single deliverable the user installs rather than a duplicate source file.

### `CardTests.state(...)`

Create typed template snapshots representing a selected outcome with optional unknown measurements.

Why: Card scenarios need real template fields and truthful terminal/running state.

### `CardTests.add(...)`

Render actual MQTT discovery templates into simulated HA entities, with optional expiry/renaming.

Why: Verify the complete discovery-to-state-attributes-to-card path.

### `CardTests.render(...)`

Render the delivered card using simulated HA registry/time helpers and an optional manufacturer selector.

Why: Autofill must be tested with HA-like inputs and strict missing-field handling.

### `CardTests.render.as_datetime(...)`

Parse an ISO timestamp with a safe fallback.

Why: Simulate the HA datetime helper for card finish-time rendering.

### `CardTests.test_yaml_card_contains_complete_manufacturer_template(...)`

Parse the retained YAML card, require its Markdown type, manufacturer selector, role recognition and destination table, and compile its Jinja content.

Why: The one shipped YAML file must be complete; a duplicate raw Markdown file is no longer needed for tests or installation.

### `CardTests.test_manufacturer_filter_and_custom_name(...)`

Check manufacturer filter and custom name.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_devices_added_automatically_and_deduplicated(...)`

Check devices added automatically and deduplicated.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_running_metrics_use_current_state_over_previous_result(...)`

Check running metrics use current state over previous result.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_expired_terminal_state_uses_retained_result(...)`

Check expired terminal state uses retained result.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_newer_run_with_lost_heartbeat_does_not_claim_old_success(...)`

Check newer run with lost heartbeat does not claim old success.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_first_run_without_retained_result_is_visible(...)`

Check first run without retained result is visible.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_incomplete_and_interrupted_are_explicit(...)`

Check incomplete and interrupted are explicit.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_unknown_progress_stays_unknown(...)`

Check unknown progress stays unknown.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_dataset_results_and_stderr_are_formatted(...)`

Check dataset results and stderr are formatted.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_renamed_entities_use_role_markers(...)`

Check renamed entities use role markers.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_actual_rclone_adapter_fields_autofill_with_renamed_entities(...)`

Check actual rclone adapter fields autofill with renamed entities.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_completion_automation_accepts_rclone_reports_and_rejects_inconsistent_json(...)`

Check completion automation accepts rclone reports and rejects inconsistent json.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.

### `CardTests.test_empty_registry_shows_setup_instructions(...)`

Check empty registry shows setup instructions.

Why: Guard this named behavior with offline assertions using the actual discovery, payload, template or application implementation.


# Manufacturer Markdown card lookup

`manufacturer-live-preview-card.yaml` contains the complete Jinja template, preset to `rclone-multithreaded-upload`. There are no Python functions in this file. The template scans `states.sensor`, resolves `device_id` and `device_attr(..., 'manufacturer')`, deduplicates devices, and finds status/last-result entities through `live_preview_role` attributes using `device_entities` and `state_attr`. It reads complete JSON attributes for fields, uses the active heartbeat or appropriate retained result, escapes diagnostics, and keeps unknown metrics unknown. This avoids hardcoded entity IDs and supports renamed entities and newly added jobs. Average speed is shown only when actual bytes and elapsed time are known.

The optional `requirements-test.txt` command (`python3 -m pip install -r requirements-test.txt`) installs Jinja2/PyYAML for local rendering checks. `python3 -m unittest discover -s tests -v` runs the full offline suite; these dependencies are not required by the upload/cleanup runtime or its MQTT publisher.

### `CardTests.test_completion_automation_accepts_rclone_reports_and_rejects_inconsistent_json.matches(...)`

Render both actual HA completion conditions against a supplied report.

Why: Valid typed outcomes must match, while boolean exit codes and inconsistent result fields are rejected.


# HA completion notification formatter

`home-assistant-mqtt-automation.example.yaml` subscribes only to the configured rclone completion topic. It reads the typed report, stops dry-run reports, derives the supplied template's validity/title/status variables and selects the existing strict success/failure branch. Both valid branches send Pushover and persistent HA notifications; malformed reports use the existing persistent-notification fallback.

The supplied `notice_message` Jinja formatter bounds identity/error/header fields, adds bounded rclone remote summaries, calculates the remaining portion of its 1,024-character budget and takes the latest stderr tail. It reserves room for the shortening marker before selecting the tail. `notice_title` limits the source title to 180 characters before adding status, keeping the result below 250 characters. This prevents long report fields from exceeding the template's notification budget while retaining complete diagnostics in the MQTT report and persistent HA failure notification. Success/failure priority and sound use the supplied template values. No scheduler or next-job commands are emitted.

### `CardTests.setUp.ha_bool(...)`

Convert HA-style true/false values with an optional default argument.

Why: The supplied formatter uses bool(false); local rendering tests must model that filter signature accurately.

### `CardTests._notification_report(...)`

Use the real completion builder to construct typed success/failure reports with rclone extensions and optional long diagnostics.

Why: Notification formatting must be tested against the shared report contract rather than a duplicate serializer.

### `CardTests._render_notification_actions(...)`

Render the actual delivered automation variables, dry-run guard, conditions and chosen notification data using native Jinja values.

Why: Tests must exercise the complete automation, not assume fixed action positions or skip metadata rendering.

### `CardTests._render_notification_actions.render_value(...)`

Recursively render YAML mappings/lists and Jinja values using the current automation context.

Why: HA notification data can contain nested templates and native booleans/integers.

### `CardTests._render_notification_actions.condition_matches(...)`

Evaluate a template condition with the HA-compatible bool filter.

Why: Dry-run and success/failure selection must use rendered truth values rather than truthy strings.

### `CardTests.test_completion_pushover_success_and_failure_use_template_metadata(...)`

Check completion pushover success and failure use template metadata.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.

### `CardTests.test_completion_pushover_long_output_is_bounded_and_keeps_latest_tail(...)`

Check completion pushover long output is bounded and keeps latest tail.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.

### `CardTests.test_completion_pushover_long_unicode_metadata_remains_within_limits(...)`

Check completion pushover long unicode metadata remains within limits.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.

### `CardTests.test_completion_dry_runs_stop_before_all_notifications(...)`

Check completion dry runs stop before all notifications.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.

### `CardTests.test_completion_inconsistent_or_nonobject_json_never_sends_pushover(...)`

Check completion inconsistent or nonobject json never sends pushover.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.

### `CardTests.test_completion_short_stderr_is_not_marked_as_truncated(...)`

Check completion short stderr is not marked as truncated.

Why: Verify this notification/card contract using the actual templates and typed completion reports, without production services.


## `tests/test_parallel_progress.py`

Offline concurrent-observer regression checks.

### `ParallelProgressTests.test_each_cleanup_rule_has_independent_pre_and_post_planning_counts(...)`

Check that each configured cleanup path has its own two planning states and count, that completing one does not advance the others, and that derived cleanup views do not inflate run totals.

Why: the user needs individual cleanup progress in addition to per-destination and whole-run progress.

### `ParallelProgressTests.setUp(...)`

Create isolated actual models/plans and a disabled local monitor for three differently configured destinations.

Why: Parallel tests require independent state and realistic denominators without a broker.

### `ParallelProgressTests.tearDown(...)`

Close the monitor, remove temporary source data and restore runtime state/cache.

Why: Concurrent fixtures must not leak state to other tests.

### `ParallelProgressTests._render_snapshot(...)`

Render the actual manufacturer card through the existing fake HA discovery registry, with renamed entity IDs. Reuses CardTests instead of duplicating HA lookup behavior.

Why: Validate the delivered template against producer snapshots without accessing an HA server.

### `ParallelProgressTests.test_each_destination_total_depends_on_its_own_config_and_overall_is_sum(...)`

Check each destination total depends on its own config and overall is sum.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests._hold_three_operations(...)`

Hold three real upload or trash-cleanup functions open through patched subprocess boundaries, inspect simultaneous observations/diagnostics and optionally render the real card.

Why: Prove actual decorators and worker context report all destinations at once, not merely a synthetic progress dict.

### `ParallelProgressTests._hold_three_operations.hold(...)`

Append worker-owned diagnostics, signal entry and wait on an independently released event.

Why: The test must be able to finish one destination while the other two remain running.

### `ParallelProgressTests._hold_three_operations.stream(...)`

Simulate one upload subprocess boundary while the real observed upload function remains active.

Why: No production transfer is needed to exercise real concurrent upload reporting.

### `ParallelProgressTests._hold_three_operations.command(...)`

Simulate backend trash cleanup while its real observed function remains active.

Why: No backend trash is emptied to exercise simultaneous cleanup reporting.

### `ParallelProgressTests.test_all_three_actual_upload_workers_are_reported_at_once(...)`

Check all three actual upload workers are reported at once.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_all_three_actual_trash_cleanup_workers_are_reported_at_once(...)`

Check all three actual trash cleanup workers are reported at once.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_manufacturer_card_shows_every_parallel_destination_and_its_steps(...)`

Check manufacturer card shows every parallel destination and its steps.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_failed_preparation_skips_its_dependents_but_leaves_post_cleanup_available(...)`

Check failed preparation skips its dependents but leaves post cleanup available.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_interruption_preserves_active_step_outcome_and_never_leaves_ghost_workers(...)`

Check interruption preserves active step outcome and never leaves ghost workers.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_zero_selected_files_are_skipped_without_a_transfer_but_keep_legacy_success(...)`

Check zero selected files are skipped without a transfer but keep legacy success.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_live_details_are_validated_detached_and_retained_in_completion_extensions(...)`

Check live details are validated detached and retained in completion extensions.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_concurrent_step_updates_keep_all_aggregate_and_destination_counts_monotonic(...)`

Check concurrent step updates keep all aggregate and destination counts monotonic.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_concurrent_step_updates_keep_all_aggregate_and_destination_counts_monotonic.capture(...)`

Capture actual monitor updates and assert core counters equal the sum of destination snapshots.

Why: Every atomic update must stay internally consistent and monotonic under concurrent writers.

### `ParallelProgressTests.test_concurrent_step_updates_keep_all_aggregate_and_destination_counts_monotonic.perform(...)`

Execute one real observation context for a configured step.

Why: The concurrency fixture must exercise the actual locking/snapshot path.

### `ParallelProgressTests.test_shared_local_snapshot_keeps_each_destination_acquisition_step_visible(...)`

Check shared local snapshot keeps each destination acquisition step visible.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_shared_local_snapshot_keeps_each_destination_acquisition_step_visible.calculate(...)`

Hold the one actual cached local-source calculation open and return a typed snapshot.

Why: Both destinations should show acquisition in progress while source scanning remains single-flight.

### `ParallelProgressTests.test_later_observations_cannot_erase_a_failed_step(...)`

Check later observations cannot erase a failed step.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.

### `ParallelProgressTests.test_observer_failure_does_not_change_a_real_upload_return_value(...)`

Check observer failure does not change a real upload return value.

Why: Verify real tracker, operation observers, worker routing or automatic card behavior without production services.


### `ParallelProgressTests.test_overall_and_table_show_upload_cleanup_and_trash_running_together(...)`

Hold distinct operation contexts active on three destinations; assert combined categories, independent current-step rows and overall/table/detail order. Confirm active categories clear when the contexts end.

Why: A single global phase cannot describe all simultaneously active destinations.

### `ParallelProgressTests.test_overview_rows_and_totals_follow_added_and_removed_config_destinations(...)`

Initialize plans for one, two, three, four and then two configured destinations, rendering each snapshot and checking exact rows and summed denominators.

Why: Adding/removing destinations must require only config changes and cannot leave stale rows.

### `ParallelProgressTests.test_finished_run_has_no_active_activity_or_stale_upload_label(...)`

Start/finish a transfer observation and settle the run; verify no running activity survives and all planned counts are settled.

Why: Finished progress must never suggest that an upload is still active.

### `ParallelProgressTests.test_overview_escapes_configured_names_with_current_details(...)`

Render a current detailed packet with its activity fields and a name containing a pipe, HTML and a newline. Check current activity/phase and safe single-cell text.

Why: Updated cards must support prior payloads and preserve Markdown table structure for user names.


# Detailed parallel observation hooks

The existing operations are called exactly once with their original arguments. observe_step wraps source acquisition, reservation/quota planning, combined delete plans, trash cleanup, upload and verification. Snapshot and configured wait calls use step contexts. The existing per-target planning loop uses one pre/post step context for each rule. The observer records no-op skips, but introduces no rclone invocation or new eligibility decision. Existing global barriers still determine actual concurrency.

The card reads details.remote_jobs under the same manufacturer/role discovery as before. It displays aggregate settled steps plus every destination's counts, all active operation labels, complete table and independent stderr tail. It retains generic item rendering when details are absent. Markdown row whitespace is controlled so step tables remain contiguous. Full details remain in status/retained-result attributes; current_item sensor state may be shortened by HA's existing 255-character template, while attributes/card retain all destinations.

The source safety audit strips only observer additions from ASTs and verifies the original operation bodies/decisions match. Existing fake-rclone tests separately prove command arguments, snapshot counts, file selection, trash behavior and stage barriers remain correct.

The progress module's `ACTIVITY_LABELS` constant maps existing operation kinds to readable overview labels, with trash steps identified by their stable IDs. Card templates loop over `details.remote_jobs` directly; they never cap destination rows with the separate generic result-list display limit.

## `process_lifecycle.py`

All process ownership belongs to the documented CLI invocation's isolated group/session. Normal barriers and command arguments remain unchanged. No process-name matching is used. Linux /proc matching is limited to that group/session, with pidfds preventing PID-reuse races when stopping individual children.

### `reset_cancellation(...)`

Start/finish an invocation without leaking cancellation into later calls.

Why: Allow repeated main() invocations and tests without carrying an old stop into a new job.

### `request_cancellation(...)`

Fence new work immediately; return whether this is the first stop request.

Why: Fence new work before the interpreter unwinds a thread-pool context.

### `notify_supervisor(...)`

Also supervise signals delivered directly to the inner job PID in htop.

Why: Signals aimed at the inner job in htop must also activate the outer owner's escalation deadline.

### `check_cancelled(...)`

Abort a queued operation before it can start more rclone work.

Why: Prevent both queued operations and subsequent commands from starting after a stop request.

### `wait_or_cancel(...)`

Keep configured waits interruptible, including waits inside pool threads.

Why: Configured delays must not hold thread-pool shutdown open.

### `StopAwareExecutor.submit(...)`

Submit a cancellation-guarded task to the existing thread pool.

Why: Preserve original function arguments while rejecting newly submitted/started work after cancellation.

### `StopAwareExecutor.submit.guarded(...)`

Check cancellation just before calling the original operation.

Why: Close the gap between submission and actual worker start for queued destinations.

### `StopAwareExecutor.__exit__(...)`

On interruption cancel queued futures and wait for running workers to finish.

Why: Cancel pending futures while joining running workers; preserve normal successful global barriers.

### `_signal_group(...)`

Signal only the new process group created for this invocation.

Why: Restrict forwarded/forced signals to the one isolated group created for this run.

### `_watch_supervisor(...)`

EOF means the outer owner died, even through SIGKILL; stop our whole group.

Why: SIGKILL cannot be caught, but closing the supervisor's ownership pipe can still be detected by the surviving job.

### `_enable_subreaper(...)`

On Linux adopt orphaned job descendants so the supervisor can reap them.

Why: Adopt orphaned ordinary descendants so the still-running Linux supervisor can reap them after cleanup.

### `_kill_group_children(...)`

On Linux force stopped-job children out of blocking pipes, retaining the job.  Match the isolated group/session and use pidfds to avoid PID reuse races. The job can then finalize its interrupted report and release its own lock.

Why: Force-stop stubborn descendants holding output pipes, while leaving the job alive for interrupted reporting and lock release.

### `_reap_group(...)`

Reap adopted children after group cleanup without waiting indefinitely.

Why: Avoid leaving adopted zombies and avoid an unlimited cleanup wait.

### `supervised_main(...)`

Run normal CLI work in an owned session with two-way death protection.  Help/version/validation and template worker hooks retain their direct behavior. A normal stop gets five seconds for cleanup/final reporting before force-kill.

Why: Cover normal exits, catchable signals and death of either app PID without altering rclone/MQTT commands.

### `supervised_main.forward(...)`

Record/forward the first signal and maintain early-child and final-job deadlines; a repeated signal forces the group.

Why: Preserve the first signal exit code, forward it to owned work, and force termination on a repeated supervisor signal.

The entry script calls `supervised_main(main)`. Its private `--internal-supervised-job READ_FD NOTICE_FD` marker is only for the supervisor's own re-exec, with inherited pipe descriptors; it is not a supported user command. Public CLI flags remain --help/-h, --config/-c, --validate-config and --version. Kill commands documented in PROCESS_LIFECYCLE.md send INT/TERM/HUP/QUIT/KILL to this invocation's supervisor or job PID. The process group and five-second deadline are fixed lifecycle behavior, not new config options.

## `tests/test_process_lifecycle.py`

### `running(...)`

Treat a zombie awaiting the system reaper as stopped, never as executing work.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `wait_for(...)`

Poll a fixture predicate until a bounded deadline.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.tearDown(...)`

Clear the shared cancellation event after each test.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.test_no_new_rclone_commands_start_after_cancellation(...)`

Set cancellation and assert both command launch paths abort without invoking subprocess functions.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.test_configured_worker_wait_is_interruptible(...)`

Hold a 120-second configured wait in a thread, cancel and require prompt completion.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.test_configured_worker_wait_is_interruptible.waiting(...)`

Record that the cancellation-aware wait raised KeyboardInterrupt in its worker.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.test_interrupted_pool_cancels_queued_jobs_and_joins_running_worker(...)`

Queue ten operations behind a held worker, cancel/unwind the pool and require every queued operation to be cancelled or interrupted.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `CancellationTests.test_interrupted_pool_cancels_queued_jobs_and_joins_running_worker.busy(...)`

Hold one running worker while queued futures accumulate, then release it for shutdown.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.stalled_job(...)`

Run the real supervisor around an unresponsive inner job and check either the five-second deadline or immediate repeated-signal escalation.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_unresponsive_job_is_forced_out_at_shutdown_deadline(...)`

Require a SIGTERM-ignoring job to be killed at the configured deadline with exit 143.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_second_stop_signal_forces_immediate_group_cleanup(...)`

Send TERM then INT to the supervisor; require prompt force-kill and preservation of the first signal code.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.run_case(...)`

Build harmless fake rclone/Paho workers and stubborn descendants, run the real CLI, signal the chosen app PID, and assert all owned processes stop while an unrelated sentinel survives. Also verify normal leftovers, exit codes, queued-work exclusion and graceful lock release.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.run_case.ready(...)`

Read atomic fixture PID records until enough actual destination and optional live-MQTT operations have started.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_sigint_stops_uploads_descendants_and_queued_destination(...)`

Send SIGINT to the supervisor during two active uploads and verify process-tree cleanup and cancellation of the third destination.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_sigterm_stops_snapshot_and_live_mqtt_process_trees(...)`

Send SIGTERM during parallel snapshots plus a live MQTT worker, including stubborn helper descendants.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_sigkill_of_supervisor_triggers_job_watchdog(...)`

Kill the outer owner with SIGKILL and require the surviving pipe watchdog to stop the job group.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_sigkill_of_inner_job_is_cleaned_by_supervisor(...)`

Kill the inner job with SIGKILL and require the supervisor to stop remaining rclone/MQTT children.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_sigterm_directly_to_inner_job_escalates_stubborn_workers(...)`

Signal the inner htop PID, verifying the notification pipe activates supervisor escalation for SIGTERM-ignoring workers.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

### `ProcessTreeTests.test_normal_exit_also_removes_lingering_descendants(...)`

Let transfers succeed while harmless descendants linger, then require normal-exit cleanup to stop them.

Why: Verify process ownership/cancellation using offline fixtures rather than production services or unrelated processes.

## `transfer_stats.py`

### `live_stats_options(...)`

Own display/logging flags when live reporting needs JSON stats on stderr.

Why: Keep per-destination measurement/aggregation observational, independent from transfer/deletion decisions and MQTT transport.

### `start_transfer(...)`

Mark an actually launched upload; unknown counters stay unknown until sampled.

Why: Keep per-destination measurement/aggregation observational, independent from transfer/deletion decisions and MQTT transport.

### `finish_transfer(...)`

Freeze the worker duration and stop advertising its final speed as current.

Why: Keep per-destination measurement/aggregation observational, independent from transfer/deletion decisions and MQTT transport.

### `record_transfer_stats(...)`

Consume valid cumulative bytes/speed/elapsedTime; reject invalid/stale samples.

Why: Keep per-destination measurement/aggregation observational, independent from transfer/deletion decisions and MQTT transport.

### `transfer_snapshot(...)`

Read locked counters with independent duration/average and stale-speed handling.

Why: Keep per-destination measurement/aggregation observational, independent from transfer/deletion decisions and MQTT transport.

## `tests/test_transfer_stats.py`

### `sample(...)`

Serialize one synthetic rclone stats line with controlled cumulative bytes, speed and elapsed time.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `wait_for(...)`

Poll a test predicate to a short monotonic deadline so subprocess and heartbeat assertions have bounded waits.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.setUp(...)`

Save shared state, create harmless temporary sources and initialize independent destination counters before each metric test.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.tearDown(...)`

Close the local live fixture, remove temporary sources and restore shared state so later tests are isolated.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_cumulative_samples_are_independent_and_combined_without_double_counting(...)`

Verify cumulative samples are independent and combined without double counting.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_invalid_packets_never_replace_measured_counters(...)`

Verify invalid packets never replace measured counters.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_stale_active_speed_becomes_unknown_and_finished_speed_is_zero(...)`

Verify stale active speed becomes unknown and finished speed is zero.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_live_logging_flags_are_owned_without_changing_transfer_filters(...)`

Verify live logging flags are owned without changing transfer filters.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_redirecting_logs_is_rejected_before_lock_or_commands(...)`

Verify redirecting logs is rejected before lock or commands.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_stream_stats_are_not_failure_diagnostics_and_failure_keeps_partial_bytes(...)`

Verify stream stats are not failure diagnostics and failure keeps partial bytes.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_monitor_failure_does_not_break_stats_reader_or_stream_exit(...)`

Verify monitor failure does not break stats reader or stream exit.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_heartbeat_refreshes_delayed_speed_without_new_worker_output(...)`

Verify heartbeat refreshes delayed speed without new worker output.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_heartbeat_refreshes_delayed_speed_without_new_worker_output.capture(...)`

Collect a publisher batch and signal the test waiter; simulate broker success without network traffic.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_card_moves_combined_metrics_to_top_and_shows_each_destination(...)`

Verify card moves combined metrics to top and shows each destination.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_real_cli_three_streams_publish_bytes_and_rates_through_fake_paho(...)`

Verify real cli three streams publish bytes and rates through fake paho.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

### `TransferStatsTests.test_real_cli_three_streams_publish_bytes_and_rates_through_fake_paho.packets(...)`

Read captured fake-broker JSON lines and extract valid live state objects for the concurrent CLI assertions.

Why: Exercise real counters, output readers, HA templates or the real CLI with harmless local fixtures and fake broker helpers; avoid production data/services.

The card's byte_size macro formats byte counts with binary units; speed_text reuses it for per-second rates and preserves Unknown. Combined average uses measured total bytes / standard runtime_seconds, while each upload average uses its own frozen process duration. The card fields stay under manufacturer/role lookup with no fixed destination list.

Live-mode upload commands use --use-json-log, --stats=1s, --stats-log-level=NOTICE, --log-level=NOTICE, --progress=false, --quiet=false, --log-file= and --syslog=false to retain structured stderr statistics. These affect logging/display, not file selection or transfer/deletion commands. Explicit copy_options redirections are rejected by load_config. No RC endpoint or additional polling process is started.

Current progress fields: progress_snapshot_locked returns only detailed workflow counters and measured aggregate metrics; remote_progress returns detailed steps, cleanup jobs, activity and transfer metrics. Summary stages remain authoritative internal results and completion remotes[].stages. No completed_stages/total_stages or duplicate live row stages are emitted. The card uses the current activity fields and failure spelling directly. Manufacturer and role discovery, pre-first-state entity lookup and expired-heartbeat handling remain necessary current behavior.

The rclone completion builder uses validated extra metadata instead of template-only dataset arguments. Live outcome groups use LivePreview.update atomically. PublishResult.status is the transport status; it stays separate from the job exit code.
