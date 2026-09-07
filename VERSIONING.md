# Versioning

The project uses a three-component version number and increments every created project version by `0.0.1`.

Rollover rule:

```text
0.0.98 -> 0.0.99 -> 0.1.0 -> 0.1.1
```

`0.0.100` is not used.

## 0.0.21

Complete CLI self-documentation release.

### Code

- Incremented the application version from `0.0.20` to `0.0.21`.
- Added `FullHelpArgumentParser`, which prints the complete CLI help text for every argparse parsing error before the specific error message.
- Added `build_cli_parser()` so parser construction, examples, flag definitions, and help text have one testable source of truth.
- Kept `parse_cli_args()` as the application entry point while allowing an optional argument sequence for direct regression testing.
- Added explicit help text for `--version`; every supported user-facing flag now has a visible explanation in `--help`.
- Added CLI examples to the built-in help text.
- Preserved argparse exit status `2` for invalid CLI syntax and all existing runtime exit behavior.

### Documentation

- Updated `README.md` to list every CLI flag, explain whether a config is required, and document the full-help-on-error behavior.
- Updated `commented_code_map.md` for the new parser class/functions and why they exist.
- Configuration schema and both config examples are unchanged because this release adds no config option.

### Tests and verification

- Added regression coverage that `--help` contains every supported flag and its explanation.
- Added regression coverage that an unknown flag exits with status `2`, prints the complete help/options list, and reports the invalid flag.
- Added regression coverage that missing required `--config` also prints the complete help/options list.
- Re-ran compile, import, CLI, config-validation, unit, and fake-rclone integration checks before packaging.

## 0.0.20

Barriered stage execution and worker-failure isolation release.

### Code

- Incremented the application version from `0.0.19` to `0.0.20`.
- Reworked PRE-UPLOAD orchestration into three explicit global stages: preparation, trash cleanup, and upload.
- Added a barrier after all pre-upload cleanup/reservation workers so no remote can start pre-upload trash cleanup while another remote is still preparing.
- Added a barrier after all eligible pre-upload trash-cleanup workers so no upload starts while another eligible trash-cleanup worker is still running or sleeping.
- Kept worker failures isolated: one failed remote does not cancel other futures in the same stage.
- Preserved prerequisite safety: a remote that fails preparation or pre-upload trash cleanup is skipped for upload, while other eligible remotes continue.
- Preserved upload-failure isolation: a failed upload does not cancel sibling upload workers.
- Split POST-UPLOAD cleanup and POST-UPLOAD trash cleanup into separate globally barriered stages.
- Preserved the safety rule that post-upload trash cleanup is not run for a remote whose planned post-upload cleanup/delete stage failed.
- Final verification still runs for all configured remotes after earlier failures.
- `trash_cleanup_threads` is now used directly for both pre-upload and post-upload trash-cleanup worker pools.
- `upload_threads` now limits only the upload stage; combined cleanup/quota stages use `max(cleanup_threads, remote_quota_cleanup_threads)`.
- Preserved the three-snapshot planner, oldest-first whole-file selection, mixed trash/hard-delete grouping, exact reservation deficit, 1 MiB safety headroom, one-byte transfer-cap allowance, and `--cutoff-mode CAUTIOUS`.

### Configuration and packaging

- Kept the active JSON schema unchanged.
- Updated `rclone-cctv-config.example.json` from the obsolete top-level `directory_cleanup_rules` layout to valid per-upload `cleanup_rules`.
- Added currently supported per-upload fields such as `name` and `buffer_size` to the CCTV example so both packaged examples reflect the active schema.
- Did not invent or recreate a production `config.json`: the uploaded v0.0.19 ZIP did not contain that file even though its old `SHA256SUMS`, tests, and verification report referred to it.
- Replaced the broken production-config regression with a regression against the actually packaged `config.example.json`.
- Rebuilt `SHA256SUMS` from the files actually present in the release archive.

### Documentation

- Rewrote `README.md` to document only current 0.0.20 behavior and usage, without old-version change history.
- Documented the new global stage barriers, failure-isolation behavior, prerequisite skips, all CLI options, all config fields, delete modes, reservation behavior, and every rclone command the application can execute.
- Rewrote `commented_code_map.md` against the current package and documented every top-level function/class plus the reason for each external command.

### Tests and verification

- Added a regression proving pre-upload trash cleanup waits for every preparation worker.
- Added a regression proving upload waits for every eligible pre-upload trash-cleanup worker.
- Added a regression proving one preparation failure does not prevent the other eligible remotes from uploading.
- Added a regression proving one upload failure does not cancel the other upload workers.
- Added a regression proving post-upload trash cleanup waits for every post-upload cleanup worker.
- Added a regression proving one post-upload cleanup failure does not cancel the other remotes.
- Updated the fake-rclone integration assertion so upload must start only after the slow remote finishes its PRE-UPLOAD listing.
- The non-destructive suite now contains 14 tests and passes in the packaged environment.
- Real destructive cloud-provider operations remain intentionally untested.

## 0.0.19

Three-snapshot remote planner and request-reduction release.

### Code

- Incremented the application version from `0.0.18` to `0.0.19`.
- Replaced repeated cleanup/reservation remote re-listing with one pre-upload `RemoteSnapshot` per remote.
- Added `planning.py` for in-memory age, `max_files`, `max_size`, `max_total_size`, and upload-reservation planning.
- Added `delete_plan.py` for one combined deletion plan per remote snapshot phase.
- Age cleanup now uses snapshot `ModTime` data instead of a separate `rclone delete --min-age` traversal.
- Cleanup decisions immediately remove selected paths from the working snapshot so later rules see simulated post-delete state.
- Pre-upload cleanup and upload reservation use the same pre-upload snapshot and normally one combined delete command per delete mode.
- Removed the repeated ten-pass reservation listing loop.
- Added a concurrent single-flight local-size cache keyed by normalized local path plus source-selection filter options.
- Identical local source/filter combinations now run `rclone size --json` once and share the result between remote pipelines.
- Post-upload age, cleanup-rule, and `max_total_size` planning now use one post-upload snapshot per remote.
- Final cleanup-rule and `max_total_size` verification now use one final snapshot per remote.
- Preserved oldest-first whole-file selection, exact byte-deficit reservation, 1 MiB safety headroom, transfer cap, `--cutoff-mode CAUTIOUS`, backend hard-delete flags, per-remote concurrency, post-cleanup after failed uploads, and final result accounting.
- Preserved mixed trash/hard-delete cleanup-rule support by grouping a combined plan by delete mode only when required.

### Normal remote listing count

A normal successful remote now executes exactly three recursive `rclone lsjson` commands:

1. pre-upload planning;
2. post-upload cleanup planning;
3. final live verification.

This count refers to recursive rclone listing commands, not provider HTTP transactions. Rclone may paginate internally, and delete/upload/optional trash-cleanup commands remain separate work.

### Configuration

- Kept `config.json` and `config.example.json` schema and values unchanged.
- Kept all four thread-limit fields for compatibility.
- Combined post-cleanup/final worker pools use the higher of `cleanup_threads` and `remote_quota_cleanup_threads`.
- Added no required configuration field.

### Verification

- Passed ten non-destructive tests.
- The real-entry-point fake-rclone integration test asserts exactly three `lsjson` calls per remote.
- The integration test asserts one local `rclone size` call for identical local source/filter combinations.
- The integration test asserts there is no standalone age-delete traversal.
- Preserved the independent remote pipeline: the fast remote uploads before the slow remote finishes its first snapshot.
- Compiled every module and imported every package module.
- Verified `--version`, `--help`, and `--validate-config`.
- Did not run destructive tests against real cloud remotes.

## 0.0.18

Full application modularization release.

### Code

- Incremented the application version from `0.0.17` to `0.0.18`.
- Kept the seven shared dataclasses in `rclone_multithreaded_upload/models.py`.
- Moved console serialization to `output.py`.
- Moved per-remote stage/result accounting and final summary handling to `results.py`.
- Moved JSON configuration parsing and validation to `config.py`.
- Added `state.py` with one shared `RuntimeState` singleton so config-loaded scalar and `Path` values remain live across imported modules.
- Moved pure size/path/time/upload-command helpers to `utils.py`.
- Moved non-streamed subprocess execution to `commands.py`.
- Moved rclone wrapper/backend resolution and backend-specific hard-delete flags to `rclone_backend.py`.
- Moved atomic lock handling and configured delays to `lock.py`.
- Moved cleanup-target generation to `targets.py`.
- Moved remote listing, managed-path filtering, deduplication, and cleanup/quota delete-list selection to `remote_files.py`.
- Moved per-rule cleanup, remote-wide quota cleanup, and trash cleanup to `cleanup.py`.
- Moved filtered local sizing and repeated pre-upload reservation logic to `reservation.py`.
- Moved streamed rclone upload execution to `upload.py`.
- Moved final rule/quota checks to `verification.py`.
- Moved all thread-pool phase runners and independent per-remote pipelines to `phases.py`.
- Moved startup summary rendering to `summary.py`.
- Moved top-level phase ordering and exit-code aggregation to package `main.py`.
- Reduced `rclone-multithreaded-upload.py` to a compatibility entry point with no application function definitions.

### Logic preservation

- Preserved the independent per-remote reservation/upload pipeline.
- Preserved the absence of a global reservation barrier: a fast remote can enter delete/upload while slower remotes are still sizing or listing.
- Preserved one recursive `rclone lsjson` root listing for managed upload-quota calculations.
- Preserved oldest-first UTC `ModTime` sorting and complete-file selection.
- Preserved exact byte-deficit reservation with a 1 MiB safety headroom.
- Preserved repeated local/remote re-reading for up to 10 reservation cleanup passes.
- Preserved the one-byte transfer-cap allowance and `--cutoff-mode CAUTIOUS` upload cap.
- Preserved `copy`, `sync`, and `move` restrictions.
- Preserved backend-specific hard-delete mappings for Drive, MEGA, and OneDrive.
- Preserved post-upload cleanup and final verification after a failed/partial upload pipeline.
- Preserved stage failure retention and `FINAL RUN RESULT` behavior.
- No destructive cleanup, reservation, upload, trash, or final-verification algorithm was intentionally redesigned.

### Configuration

- Preserved `config.json` values from v0.0.17 unchanged: four threads in each category, GDrive/Mega/OneDrive names, and `12G` / `12G` / `50G` managed limits.
- Preserved `config.example.json` and the configuration schema unchanged.
- Added no required configuration option.

### Documentation

- Preserved the required README disclaimer at the absolute top.
- Preserved the project-specific remote Data Loss Warning with the disclaimer.
- Updated README project layout, runtime-state explanation, independent-pipeline behavior, and regression-test instructions.
- Replaced the old single-file code map with a module-by-module v0.0.18 function and command map.

### Verification

- Compiled the compatibility entry point and every package module.
- Imported every package module successfully.
- Verified `--version` reports `0.0.18`.
- Verified `--help` and `--validate-config` succeed.
- Verified config validation does not create a lock or execute cleanup/upload logic.
- Added and passed nine non-destructive tests: eight focused logic/concurrency regressions plus one full-flow fake-rclone integration test. Coverage includes size parsing, size-filter forwarding, one-root managed listing, deduplication, oldest-full-file cleanup selection, exact reservation deficit, upload command construction, independent pipeline concurrency, current production config values, the real entry point, reservation deletion, upload, post-upload cleanup, final verification, and final result reporting.
- Did not run destructive end-to-end tests against real cloud remotes. Provider-specific production behavior still requires a controlled live run with the user's configured rclone remotes.

## 0.0.17

README remote-backup data-loss warning correction release.

### Code

- Incremented the application version from `0.0.16` to `0.0.17`.
- Did not move, redesign, or intentionally change cleanup, reservation, upload, trash, locking, result, backend-delete, configuration, or final-verification logic.
- Preserved the current modularization boundary: all seven dataclasses remained in `rclone_multithreaded_upload/models.py`, and all 72 application functions remained in `rclone-multithreaded-upload.py`.

### Documentation

- Added a project-specific `Data Loss Warning` directly after the required top disclaimer and before the project title.
- Explicitly warned that cleanup, age-based deletion, file-count limits, directory-size limits, and total remote-size enforcement can permanently delete remote files.
- Explicitly warned that incorrect paths, filters, age settings, or limits can delete valid online backup copies and leave the user without a usable online backup.
- Stated that a remote managed by this script should not be the user's only backup.

## 0.0.16

README disclaimer update release.

- Replaced the previous README disclaimer block with the updated Disclaimer & Responsibility and AI-assisted/vibe-coded disclaimer text.
- Kept the disclaimer as the absolute first README content.
- No application logic or module boundary intentionally changed.

## 0.0.15

README disclaimer/compliance release.

- Added the required AI-assisted/vibe-coded experimental-software disclaimer at the absolute top of `README.md`.
- No cleanup, reservation, upload, trash, locking, result, backend-delete, configuration, or final-verification logic intentionally changed.

## 0.0.14

First modularization release: safest shared-model extraction.

- Added the internal `rclone_multithreaded_upload` package.
- Added `rclone_multithreaded_upload/models.py`.
- Moved all seven existing dataclasses into `models.py` without redesigning their fields or defaults.
- Kept all 72 top-level application functions in `rclone-multithreaded-upload.py`.
- Tracked `output.py` as the next recommended extraction after dependency review.

This packaged `VERSIONING.md` records the modularization history from v0.0.14 onward. Older release details remain in the prior project history; v0.0.19 does not rewrite those historical entries.
