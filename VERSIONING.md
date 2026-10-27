# Versioning

The project uses a three-component version number and increments every created project version by `0.0.1`.

Rollover rule:

```text
0.0.98 -> 0.0.99 -> 0.1.0 -> 0.1.1
```

`0.0.100` is not used.

## 0.0.37

Owner-requested second cleanup after per-destination transfer reporting.

- Incremented 0.0.36 to 0.0.37; preserved every original and previous-release file and both complete current config examples. Existing legacy mqtt config/option converters and aliases remain absent, and obsolete/unknown config keys remain rejected. No active configuration option was removed in this pass.
- Replaced six repeated future-submission/join/error-handling blocks with run_remote_workers. Each phase retains its original candidate selection, worker limit, complete barrier and prerequisite skip rules. Ordinary exceptions stay isolated per destination; cancellation still propagates through StopAwareExecutor. Reused the shared STAGES tuple for completion/result classification instead of repeating literals.
- Removed unused template-only LivePreview.record_dataset, completion _dataset_list and its planned/skipped/failed dataset keyword arguments, and PublishResult.sent. Current rclone code uses atomic bulk LivePreview.update, validated completion extra metadata and PublishResult.status. Standard JSON fields and optional-array validation remain intact.
- Removed old four-stage live compatibility counters completed_stages/total_stages and duplicate live remote_jobs[].stages; detailed step/activity/transfer fields remain the progress source. Internal safety stages, console results and completion remotes[].stages remain intact.
- Removed HA card support for the historical failed spelling and old activity-less destination packets. Current manufacturer/role matching, first-state lookup, expired-heartbeat handling and null-measurement behavior remain. Use the shipped app/card together.
- Updated README to current usage, MQTT/live field guides, VERSIONING and every affected/new code-map entry. Regenerated the single success/failure and live packet examples; preserved the exact owner disclaimer and lean example inventory.
- Retargeted tests to current APIs, retained wire-array/detachment/validation coverage and added all-six-stage crash isolation plus cancellation/empty-barrier checks. Compared old/new orchestration across 648 deterministic three-destination scenarios including success, false/None returns and exceptions. The full offline suite, compile/CLI/config/card checks and ZIP manifest/hash checks are recorded in verification_report.txt.
- Before/after runtime Python: 4,683 -> 4,546 physical lines; 3,868 -> 3,739 code lines (129 fewer). Tests: 3,770 -> 3,828 physical lines; 3,389 -> 3,441 code lines. All Python: 8,453 -> 8,374 physical and 7,257 -> 7,180 code lines. Including both HA YAML files: 8,803 -> 8,724 physical code-file lines (79 fewer). No tests or required files were dropped to obtain the reduction. Counting methodology and full data are in RELEASE_MANIFEST.json.

## 0.0.36

Measured per-destination transfer bytes/rates and combined top-level metrics.

- Incremented 0.0.35 to 0.0.36. Added transfer_stats.py for owned live JSON statistics flags, strict numeric cumulative-sample validation, per-process start/finish/duration tracking, non-duplicating counters and stale-speed handling.
- Upload streams parse each rclone process's numeric bytes/speed/elapsedTime. Healthy stats do not fill failure diagnostics; ordinary output and errors retain existing handling. Process exit freezes its duration/average and zeros its current rate, including failures/interrupts.
- Added per-remote metrics to the existing locked progress snapshot and summed them into standard live transferred_bytes/bytes_per_second and detailed/completion progress. Pending/unlaunched destinations contribute zero; launched unknown metrics remain null. Current totals do not include a finished worker's old speed.
- Live upload commands own one-second JSON stats and display/logging flags, including environment log redirection overrides; live-enabled configs reject explicit redirecting log-file/syslog options. Non-live logging options, source filters, transfer concurrency, exact file lists and all deletion/lock/barrier/process-supervision behavior are retained.
- Added an optional before_publish local refresh callback to the live module, called outside its lock. This refreshes metrics/staleness before heartbeat packets without adding a new thread/process or network work on the transfer path. Standard wire fields/discovery/manufacturer identity remain unchanged.
- Moved aggregate bytes/combined speed/run average/runtime to the overall card block at the top, added per-destination bytes/current speed/average, and removed the former bottom metrics block. The run average is total bytes divided by job runtime; per-worker averages use that process's lifetime. Added shared unit-formatting Jinja macros.
- Removed redundant stats display flags from config example values while preserving all canonical keys; regenerated the single success/failure and live packet files with measured sample counters. Updated current usage and every affected/new function/command explanation, preserving the owner disclaimer and lean package inventory.
- Added offline metric/flag/invalid/stale/heartbeat/stream/card and full three-process CLI + fake Paho tests, including finishing one destination while others continue. Existing signal/shutdown and operation safety tests remain. Real broker/cloud/rclone binary operation remains untested where unavailable; package verification records exact results and limits.

## 0.0.35

Owner-requested removal of obsolete compatibility code and redundant helpers.

### Code/config/API changes

- Incremented 0.0.34 to 0.0.35. Removed the root mqtt translator, old timeout/keepalive/retain/TLS option handling and historical topic/QoS defaults. Only completion/live config sections are supported for reporting.
- Replaced parse_mqtt_config with parse_completion_config and model-level MqttConfig alias imports with the canonical MQTTConfig. Internal runtime state now uses completion. Removed the unused package __version__ alias; VERSION remains the single app version constant.
- Reused one unknown-key validator for root, thread limits, uploads, cleanup rules and reporting. Unsupported/obsolete fields fail validation before work instead of being ignored. Current config defaults/options and both complete examples remain unchanged.
- Removed unused compatibility helpers get_upload_remote_quota_entries, get_filtered_local_upload_size, join_relative_path, build_mqtt_status_payload and the unused RemoteQuotaFile model. Removed the unused sleep_after_step helper and one-line publish_result_payload wrapper; current callers use the real implementations.
- Replaced duplicate source/transfer-option scanners with split_copy_options, preserving option order/values and source-cache keys/exact upload selection. Removed a write-only reservation-byte map while retaining the locked planned file list and computed quota/summary values.
- Removed cleanup.py's obsolete execute_delete_plan re-export/__all__ and unused imports; phase callers import the actual delete-plan implementation directly.
- Removed refresh_live's ignored positional current-item argument; current callers request an explicit clear_current flag at finish. Progress still summarizes every active destination atomically.
- Removed duplicate completion fields script_name/application/application_version; standard name/job/version remain, along with event/remotes/failed_remotes/progress. HA automation already uses the standard fields. Full live JSON/manufacturer/role behavior is unchanged.
- Preserved actual backend wrapper handling, deletion eligibility/modes, quota selection, stage barriers, locking, signal supervision and standard MQTT/HA contracts.

### Documentation/counts/validation

- Updated current README/config/API/JSON explanations and all affected function/command entries, retaining the exact owner disclaimer and the lean example-file inventory.
- Retargeted tests from deleted adapters to canonical APIs; retained source-cache coverage and added rejection of obsolete/unknown keys at every config level. Extended option-partition checks; no safety/process tests removed.
- Regenerated the one success/failure packet file and one live packet file. Both config examples and retained HA YAML files remain complete.
- Recorded reproducible before/after physical Python lines and code lines (excluding blank/comment/docstring-only lines), separating runtime and tests, in verification_report.txt and RELEASE_MANIFEST.json. Counts cover all shipped Python files; docs/JSON/YAML are separately identified.
- Verified compile/basic CLI/config checks and the full offline suite; packaged the complete clean project with original/previous inventory checks, hashes and count comparison. No real broker, HA or cloud data was exercised.

## 0.0.34

Owner-requested package/example cleanup.

- Incremented 0.0.33 to 0.0.34; runtime behavior, safety, termination handling and MQTT/HA contracts are unchanged.
- Kept both complete config examples, one payload-examples.json file containing success/failure packets, and one live-payload-examples.json packet showing three independently progressing destinations. Reduced repeated sample snapshots; generated examples through the real tracker/adapter without commands or broker I/O.
- Removed ha-discovery-examples.json, parallel-progress-examples.json and parallel-progress-preview.md as redundant generated/reference artifacts.
- Retained manufacturer-live-preview-card.yaml as the single canonical live card. Removed duplicate manufacturer-live-preview-card.md, environment-specific asus-frigate-live-preview-card.yaml and the superseded fixed-entity home-assistant-live-dashboard.example.yaml. Custom manufacturers now use the documented one-line selector edit; installed cards remain compatible.
- Consolidated unique PARALLEL_PROGRESS.md schema/status guidance into LIVE_PREVIEW.md and retained the step-total formula in README.md. Removed the separate TEMPLATE_COMPATIBILITY.md development audit; current contracts/provenance remain in guides and release metadata.
- Updated tests to render the retained YAML directly and check the complete template. Updated current usage, function/command explanations and ignore entries; preserved source, dependencies, test suite, owner disclaimer, required docs and release integrity files.
- This release intentionally removes eight redundant files under the owner's cleanup request. The release manifest records each removal against original/previous inventories with its reason, rather than claiming every old optional artifact was retained. Previous delivered versions remain available unchanged.
- Verified compile/CLI/config/schema/card checks and the complete offline suite, then packaged a clean full ZIP with hashes. No production broker, cloud data or live HA instance was exercised.

## 0.0.33

Owned process-tree termination and bounded cancellation.

### Code

- Incremented 0.0.32 to 0.0.33. The compatibility CLI now supervises normal jobs in a fresh POSIX session/process group; help/version/validation and private publisher hooks retain direct behavior.
- Added process_lifecycle.py: supervisor signal forwarding, five-second shutdown deadline, repeated-signal force kill, job-owner pipe watchdog for supervisor SIGKILL, inner-job signal notification pipe, owned-group cleanup, Linux pidfd child escalation and child-subreaper adoption/reaping.
- Added cooperative cancellation checks before rclone launches, cancellation-aware pool barriers that discard queued work, and interruptible configured waits. Existing successful execution order, commands, deletion policies, quota selection and locks remain intact.
- SIGINT/SIGTERM/SIGHUP/SIGQUIT stop signals request cancellation and preserve signal exit codes; repeated inner signals do not interrupt finalization again. Final MQTT/live interruption reports remain best-effort within the shutdown window. Completion/live template files and success/failure JSON/card formats are unchanged.
- The supervisor handles lingering group members on normal or abnormal job exit. A killed supervisor is detected by pipe EOF; a killed job is detected by its supervisor. SIGKILL still cannot execute notifications or lock cleanup.

### Documentation and validation

- Added PROCESS_LIFECYCLE.md and updated current README usage, process/lock behavior, function/command map and compatibility notes. Preserved the exact owner disclaimer, all original files and complete configuration examples; no config fields were added.
- Added 11 lifecycle/cancellation tests using the real CLI and harmless rclone/Paho process fakes, including descendants that ignore stop signals, both displayed app PIDs, a shutdown deadline, repeated signals, normal leftovers and an unrelated sentinel.
- Re-ran the complete offline suite, compile/CLI/config checks and source safety comparisons. Packaged the full project with original/previous manifest checks, hashes and a readable verification report. Real cloud/broker operation and frozen/other-platform execution remain untested.

## 0.0.32

Dynamic destination overview and combined live activity summary.

### Code and card

- Incremented the application version from 0.0.31 to 0.0.32.
- Added readable activity metadata and active counts to existing locked step snapshots, per destination and overall. Explicitly distinguish Upload, Cleanup and Trash cleanup; also identify snapshots, quota planning, waits and verification. Derive every label/count from currently running observations; idle/final snapshots retain no stale active category.
- Added the compact manufacturer-card destination table beneath overall progress. Rows follow the effective `upload_directories` list, displaying configured names, current operations, independent settled/total counts, percentages and states. Detailed per-destination steps/cleanup-path views remain below it.
- Added combined actual activities to the top phase display, table-cell escaping, prior detailed-payload fallback, and a failure marker that remains visible during permitted later work.
- Kept manufacturer/role recognition and auto-filled entity lookup, custom ASUS preset, completion/live independence, completion module/automation, transport, rclone operations, safety rules and stage barriers unchanged. No configuration options were added.

### Documentation and validation

- Updated current usage and live/progress guides, all affected function explanations and code-map test entries; preserved the owner's exact disclaimer and complete config examples.
- Updated synthetic JSON/rendered examples to OneDrive, GDrive and Mega, including simultaneous upload/cleanup/trash activity.
- Extended offline tests for dynamic addition/removal of destinations, independent totals, mixed activities, overview placement, old payloads, escaped names and removal of stale active labels. Existing concurrent real-operation observers and fake-rclone entrypoint checks remain in the suite.
- Included a full clean project ZIP, original/previous manifest comparison, file/archive hashes and verification report. No production broker, HA instance or cloud data was accessed.

## 0.0.31

Concurrent per-destination workflow progress and whole-run aggregation.

### Code

- Incremented the application version from 0.0.30 to 0.0.31.
- Added StepRunResult and per-remote observation/diagnostic fields, plus progress.py for config-derived step plans, thread-safe monotonic observations, worker-local diagnostic routing, final settlement and detached snapshots.
- Initialized detailed plans through the existing run-result/cleanup-target builders. Added observer-only operation decorators and contexts for snapshots, each cleanup rule, reservation, delete plans, trash cleanup, wait, upload and final verification. No-op/blocked operations have explicit skip reasons.
- Kept the existing four stage results and all prerequisite/barrier/command decisions. Stage failures annotate active observations and skip only remaining steps in that stage; unrelated destinations and permitted post-cleanup continue.
- Changed live generic counters to detailed steps and added atomic details.remote_jobs, independent destination totals/current operations, all-active-worker summary and per-destination stderr tails. Added per-cleanup-path pre/post planning counters as non-duplicating views of the same steps.
- Extended the supplied live module only with optional JSON-object details validation/detachment/merge. Standard core fields, manufacturer discovery, role attributes, retention and timeout-bound workers remain intact. Completion template is unchanged; completion adapter adds progress extensions without changing standard outcome fields.
- Added final unresolved-step settlement and startup/config-validation overall/per-destination counts. Monitor failures remain non-fatal to actual work.

### Card, documentation and tests

- Extended the manufacturer Markdown/YAML card with per-destination counts, active operations, complete step table and diagnostics, with generic-job fallback. Preserved exact manufacturer/role recognition. Supplied a separate updated card preset to the user's custom manufacturer.
- Updated current guides, function/command map, version history and examples. Added PARALLEL_PROGRESS.md and synthetic JSON/rendered examples. Config options/settings and the owner's exact disclaimer remain intact.
- Added concurrent real-operation observer tests holding three uploads and three trash cleanups open, isolated diagnostic routing, independent denominators, all-destination rendering, shared-source cache visibility, monotonic aggregates, skip/failure/interruption states and non-fatal monitoring.
- Updated earlier coarse-progress assertions to detailed totals while retaining legacy stage checks. Real fake-rclone/publisher integrations verify complete detailed snapshots and unchanged three-listing/barrier behavior.
- Compared original operation ASTs after removing observer-only additions; operation decisions/bodies match. Protected config/command/lock/backend/listing/target/util modules remain byte-identical. Full checks, limits, clean ZIP manifest and hashes are recorded in verification_report.txt.

## 0.0.30

Latest supplied MQTT-template compatibility audit and bounded HA/Pushover notifications.

### Code and automation

- Incremented the application version from 0.0.29 to 0.0.30. No operational Python code changed.
- Verified the supplied completion 1.1.0/live 1.2.0 modules still match byte-for-byte; reused them unchanged.
- Updated the existing combined HA automation with the new supplied Pushover variable/message formatter, bounded title and message, latest stderr tail and explicit shortening marker. Kept rclone remote summaries inside that dynamic budget.
- Reused the template's notification priority/sound fields. Preserved strict typed success/failure gates, dry-run suppression, topic, Pushover action and full persistent HA failure notification. MQTT report error/stderr fields are not shortened by this notification formatting.
- Verified the supplied manufacturer Markdown/YAML card is unchanged in logic. Kept its rclone manufacturer selector and remote labels, auto-discovery, full attributes, role-based field lookup, renamed-entity support and independent live/completion channels.
- No upload, cleanup, quota, reservation, backend, trash, lock, stage-barrier, discovery-ID, sequencing or scheduler behavior changed.

### Documentation, examples and checks

- Updated current README/MQTT usage, payload/discovery example versions and complete function/command map. Preserved the exact owner disclaimer.
- Added TEMPLATE_COMPATIBILITY.md with exact comparison findings and source archive hash. All canonical config options remain present and config settings are retained.
- Added rendered automation checks for success/failure metadata, long stderr/latest tail, long Unicode metadata, full persistent diagnostics, preview suppression, malformed/inconsistent reports and short output. Existing card/adapter/full-flow tests are reused.
- Preserved all original and previous-release files; regenerated clean ZIP, preservation manifest, verification report and checksums. Detailed results and untested live integrations are in verification_report.txt.

## 0.0.29

Updated Syncerate live discovery and automatic manufacturer-based HA Markdown card.

### Code and reporting

- Incremented application version from 0.0.28 to 0.0.29 and added a shared APPLICATION_NAME constant reused by reporting and live configuration.
- Replaced ha_live_preview.py with the newly supplied template 1.2.0 byte-for-byte. It adds validated manufacturer configuration, manufacturer in discovery/state, full status/result attributes and stable live_preview_role markers.
- Defaulted the rclone adapter's manufacturer to rclone-multithreaded-upload, including when existing configs omit the new option. Explicit valid manufacturer overrides remain supported; matching card selectors are required for overrides.
- Kept configured script_name as the individual job/device name and preserved device IDs, unique IDs, topics and the independent completion/live enable switches.
- Added the effective manufacturer to the enabled live startup summary.
- Reused the supplied, unchanged completion module 1.1.0 and existing rclone completion adapter. Verified real worker success and partial-upload failure payloads against the JSON contract and HA automation conditions.
- No upload, cleanup, quota, reservation, backend, prerequisite, trash, stage-barrier or lock safety logic was changed.

### Configuration and documentation

- Added live.manufacturer to both complete config examples, preset to the script/application name.
- Added manufacturer-live-preview-card.md and its exact YAML wrapper, both preset to rclone-multithreaded-upload. The card discovers matching jobs and fields automatically, supports renamed entities, and reads real status/result attributes. Rclone outcomes are labelled as remotes.
- Preserved the existing notification automation and optional entities/gauge dashboard.
- Added actual rclone HA discovery JSON examples and optional card-test requirements.
- Updated current README/MQTT/live guides, payload example versions, complete function/command map, verification report, preservation manifest and checksums. Preserved the owner's disclaimer verbatim.

### Verification

- Added supplied manufacturer/role-marker tests and card rendering tests, actual rclone-adapter-to-card checks, HA completion-gate checks, and a partial-upload-failure variant reusing the existing real-entrypoint integration fixture.
- Exact results and untested external integrations are in verification_report.txt. Full ZIP file preservation is checked against the original project and previous release.

## 0.0.28

Documentation completeness and verified release workflow.

### Code

- Incremented the application version from 0.0.27 to 0.0.28; the existing --version option continues to use the single package version constant.
- Expanded the existing CLI help to explain independent completion/live JSON switches, the effects of normal copy/sync/move/cleanup runs, and the limits of --validate-config. No public flags were added or removed.
- No upload, deletion, quota, reservation, stage-barrier, backend, lock or MQTT runtime logic was changed in this version. Existing modules and tests are reused.

### Documentation, examples and release packaging

- Replaced overlapping README disclaimers with the owner's supplied wording verbatim, retained the relevant remote-data warning, added setup steps, corrected current exit/validation behavior and documented upload commands.
- Kept README focused on current app usage; release history remains in this file. Detailed MQTT/live guides remain available and now explain HA device/entity names explicitly.
- Expanded commented_code_map.md with individual explanations and reasons for every explicit production function/method, including template internals, adapter functions and private worker commands; included a complete offline-test function inventory.
- Audited both JSON config examples against every canonical configurable field; both were already complete and are retained. Refreshed payload example versions.
- Added AGENTS.md to preserve the owner's maintenance rules, RELEASE_MANIFEST.json to report original/previous-release file preservation, and updated verification_report.txt and SHA256SUMS.
- Preserved all files from the original supplied project and previous release, and excluded bytecode, caches and temporary files from the final ZIP.

### Verification

- Results, exact checks and untested integrations are recorded in verification_report.txt. Final ZIP membership and hashes are checked against both supplied original and v0.0.27 manifests.

## 0.0.27

Syncerate-template completion JSON and independent Home Assistant live preview.

- Vendored the supplied template 1.1.0 `mqtt_status.py` and `ha_live_preview.py` unchanged.
- Added canonical independent `completion` / `live` JSON sections and validation, including config-relative TLS certificate paths. Both default to disabled.
- Final reports now include the complete typed template contract and preserve rclone remote/stage diagnostics. Success error is an empty string. Success suppression is independent of live monitoring.
- Added live discovery, heartbeat, settled-stage progress, captured diagnostic tails, remote outcomes and retained last result; unmeasured transfer totals/speed stay null.
- Added shared run IDs, runtime exception and interrupt reporting, bounded publishing workers and lock release after reporting. Ordinary notification errors preserve the job code.
- Legacy `mqtt` configs are translated; see `MQTT.md` for removed retain/insecure behavior, keepalive handling and canonical defaults.
- Updated both JSON configs, HA notification automation, separate live dashboard, payload examples, code map and offline verification.
- Preserved reservation, cleanup, upload, quota, trash barriers and final verification algorithms. Expanded tests to cover adapter lifecycle and real publisher subprocesses alongside fake-rclone integration.

## 0.0.26

MQTT legacy `loop_start()` compatibility fix release.

### Code

- Incremented the application version from `0.0.25` to `0.0.26`.
- Fixed final MQTT result publishing for Paho MQTT implementations where a successful `client.loop_start()` returns `None` instead of `MQTT_ERR_SUCCESS` / `0`.
- The MQTT publisher now accepts both documented success forms: `None` for legacy behavior and `MQTT_ERR_SUCCESS` for newer behavior.
- Explicit non-success return codes are still treated as MQTT transport failures.
- Preserved MQTT as notification-only: publication failure still cannot rewrite a successful backup/cleanup result or process exit code.
- No cleanup, reservation, upload, quota, delete-mode, locking, or final-verification behavior was changed.

### Documentation and configuration

- Updated `README.md` MQTT behavior documentation to explain compatibility with both Paho return conventions.
- Updated `commented_code_map.md` to document why `publish_result_payload()` accepts both `None` and `MQTT_ERR_SUCCESS` from `loop_start()`.
- No configuration fields were added, removed, or renamed. Existing `config.example.json`, `rclone-cctv-config.example.json`, and user configs remain compatible.

### Tests and verification

- Added a regression test that simulates legacy Paho behavior where `loop_start()` returns `None` and verifies that publishing continues, completes, and stops the network loop cleanly.
- Retained the existing MQTT publish-path test for the newer explicit `0` / `MQTT_ERR_SUCCESS` return convention.
- Full verification results for this packaged release are recorded in `verification_report.txt`.

## 0.0.25

Named-run and optional MQTT result notification release.

### Code

- Incremented the application version from `0.0.24` to `0.0.25`.
- Added root `script_name` configuration with default `rclone-multithreaded-upload`; the configured name is shown in the startup summary and included in the final MQTT JSON payload.
- Added optional `mqtt` configuration and a dedicated `mqtt.py` module. MQTT remains disabled by default and does not import the optional paho-mqtt dependency unless publishing is enabled.
- Added stable schema-versioned final JSON payloads containing overall `status`/`success`, process exit code, UTC timestamp, script name, application version, friendly failed-remote names, per-remote stage states, structured per-stage errors, and a combined top-level failure `error` string suitable for Home Assistant templates.
- Added MQTT broker authentication, QoS 0/1/2, retain control, client ID, keepalive, TLS, optional CA file, TLS-insecure override, publish timeout configuration validation, and publish-topic validation that rejects MQTT wildcards.
- MQTT result publication runs only after a normal execution has reached final result aggregation. `--validate-config` does not publish.
- MQTT transport errors are logged but deliberately do not rewrite the backup/upload/cleanup result or process exit code; notification transport is observational rather than a destructive/runtime prerequisite.
- Startup summaries expose MQTT connection settings without ever printing the configured password.
- Preserved all v0.0.24 quota selection, strict newest-first cutoff, hard-delete/trash distinctions, cleanup barriers, partial-upload handling, and final verification behavior.

### Home Assistant and configuration

- Added MQTT examples to both packaged JSON configs. Existing configs remain compatible because `script_name` is optional and MQTT defaults to disabled.
- Added `requirements-mqtt.txt` containing the optional `paho-mqtt` dependency.
- Added `home-assistant-mqtt-automation.example.yaml`, which subscribes to the example topic, branches on `trigger.payload_json.status`, sends Pushover on success/failure, and also creates a persistent Home Assistant notification. Failure notifications include the script name, failed remote names, and captured error text.
- Default/example result messages are non-retained so reconnecting Home Assistant does not replay a historical result as though it were a new run.

### Tests and verification

- Added config-validation coverage for `script_name`, null/new-field handling, enabled MQTT without a host, QoS range validation, publish-topic wildcard rejection, and startup-summary password secrecy.
- Added payload coverage for both successful and failed runs, including captured remote/stage error text.
- Added coverage proving disabled MQTT does not attempt to import paho-mqtt.
- Added publish-path coverage for configured broker, credentials, topic, QoS, retain, keepalive, client ID, timeout, and Paho 2.x callback API client construction.
- Added coverage proving MQTT transport failure is reported without raising into the completed backup result.
- Full automated suite for v0.0.25: 41 tests.

## 0.0.24

Strict newest-first quota cutoff release.

### Code

- Incremented the application version from `0.0.23` to `0.0.24`.
- Changed quota-managed local file selection from gap-filling to a strict contiguous newest-first prefix.
- Selection now stops immediately when the first next complete file would exceed the remaining upload budget. That file and every older file are deferred to a later run; the selector never scans farther for a smaller older file.
- Preserved complete-file transfers, the frozen generated `--files-from0` upload set, reservation safety headroom, oldest-first remote cleanup, and the removal of runtime `--max-transfer` / `--cutoff-mode`.
- If the newest file itself does not fit, quota planning selects zero files and the upload stage succeeds as a no-op without invoking rclone. This also avoids unsafe empty-list `sync` behavior. The pipeline then continues through post-upload cleanup and final verification normally.
- Added explicit reservation output showing the selection policy, whether the quota cutoff was reached, and how many files/bytes remain at and after the cutoff.
- Preserved all hard-delete versus trash-mode distinctions and trash-cleanup activity tracking.

### Documentation and configuration

- Updated `README.md` and `commented_code_map.md` to document the strict stop-at-first-non-fitting-file policy and zero-selection no-op behavior.
- No config option was added or removed. Existing v0.0.23 configuration remains compatible.

### Tests and verification

- Replaced the former gap-filling test with strict cutoff coverage proving smaller older files are not selected after the first non-fitting file.
- Added coverage proving selection stops immediately with an empty set when the newest file itself does not fit.
- Added coverage proving an empty quota selection is a successful no-op and does not start rclone.
- Updated the end-to-end fake-rclone capped-upload regression to prove only the contiguous newest-first prefix is uploaded and older smaller files after the cutoff remain deferred.

## 0.0.23

Quota-managed complete-file upload selection release.

### Code

- Incremented the application version from `0.0.22` to `0.0.23`.
- Removed the runtime `--max-transfer` / `--cutoff-mode CAUTIOUS` upload cap that caused successful partial transfers to terminate with rclone exit code `8` when the configured transfer ceiling was reached.
- Replaced aggregate-only quota sizing with one exact filtered local `rclone lsjson` snapshot containing path, size, and `ModTime` for every candidate file. Identical local source/filter combinations still use one concurrent single-flight scan per run.
- Quota-managed uploads now calculate an upload byte budget of `max_total_size - reservation_safety_headroom_bytes`.
- When the filtered candidate set exceeds that budget, complete files are selected newest-first by `ModTime`; a file is included only when it fully fits in the remaining budget. Oversized files are skipped and smaller later files may still fill remaining capacity.
- The remote reservation planner now reserves exactly the selected complete-file bytes plus safety headroom and frees oldest managed remote files only when needed to cover that selected set.
- The exact selected local paths are frozen between PRE-UPLOAD planning and the later UPLOAD barrier so newly-created source files cannot silently exceed the already-planned reservation.
- Quota-managed upload commands now use a generated NUL-separated `--files-from0` list. Source-selection filters are removed from the final transfer command because rclone ignores ordinary filters when `--files-from*` is active; non-filter options such as stats and transfer concurrency remain unchanged.
- Generated upload-list names reuse the collision-resistant `remote_name_from_path()` helper and are written below the configured `delete_list_dir`.
- Preserved normal non-zero upload error handling: partial-transfer failures remain failures and are not reclassified as success.
- Preserved all hard-delete versus trash-mode behavior, trash-cleanup activity tracking, stage barriers, sibling-worker failure isolation, post-upload cleanup after failed uploads, and final quota verification.
- Added a safety validation rejecting `--delete-excluded` for quota-managed `sync`, because over-budget paths are deliberately excluded from the generated upload set and must not be deleted from the destination merely because they were skipped for that run.

### Documentation and configuration

- Updated `README.md` to document exact local source snapshots, newest-first complete-file selection, generated `--files-from0` uploads, the removal of transfer ceilings, and quota-managed sync safety.
- Updated `commented_code_map.md` for the new local snapshot models, reservation helpers, upload-list writer, state tracking, planning behavior, and external rclone command map.
- No config option was added or removed. Existing `max_total_size`, cleanup rules, delete modes, filters, and thread settings remain compatible.

### Tests and verification

- Expanded the automated suite from 24 tests in v0.0.22 to 29 tests.
- Added explicit below-limit, exactly-at-limit, and above-limit complete-file selection coverage.
- Added newest-first selection coverage, including the case where an oversized newer file is skipped while a smaller older complete file still fits.
- Added encrypted-remote command construction coverage proving quota-managed uploads use `--files-from0` and do not emit `--max-transfer` or `--cutoff-mode`.
- Added partial/error handling coverage proving rclone exit code `8` remains an upload failure rather than being treated as a capped-upload success.
- Added config-validation coverage for unsafe quota-managed `sync --delete-excluded`.
- Added an end-to-end fake-rclone capped-upload regression proving the real application entry point uploads only the newest complete files that fit the budget and succeeds without a transfer ceiling.

## 0.0.22

Delete-mode safety, collision-resistant delete lists, and strict config validation release.

### Code

- Incremented the application version from `0.0.21` to `0.0.22`.
- Made generated delete-list remote-name fragments collision-resistant by appending the full SHA-256 digest of the original `remote_path` to a bounded readable filename component. Distinct legacy-colliding paths such as `a:b/c` and `a_b:c` now generate different files.
- Preserved separate trash and hard-delete groups in combined plans and added per-run tracking for **actual script-managed trash activity**.
- A successful planned trash-mode delete now marks only its exact reservation or post-cleanup stage as having used trash. Successful hard-delete commands never set that marker.
- `rclone cleanup` no longer decides from the upload-level `delete_to_trash` default alone. It now requires `empty_trash=true` plus tracked script-managed trash activity for the relevant stage.
- Cleanup-rule `delete_to_trash` overrides are therefore respected in both directions: an explicit trash rule can trigger cleanup even when the upload default is hard delete, while hard-delete-only planned work does not empty unrelated backend trash even when the upload default is trash.
- Preserved `sync` semantics by recording when an `rclone sync` configured for trash mode is actually started; post-upload trash cleanup can therefore handle destination files that sync may have moved to trash, including partial work before a failed sync. A configured sync that never starts does not set the marker.
- Exact duplicate upload `remote_path` values now produce an explicit safety `WARNING` and make config loading fail before runtime state, worker scheduling, locking, or rclone activity. This prevents duplicate destinations from collapsing dictionaries keyed by `remote_path` or receiving concurrent destructive work.
- `delete_min_age` is now fully parsed during `load_config()`, so `--validate-config` rejects malformed age/duration/timestamp values before execution.
- Replaced permissive size parsing with strict whole-number syntax. Dedicated size settings now accept only `K`, `KB`, `M`, `MB`, `G`, `GB`, `T`, or `TB` suffixes and reject malformed/ambiguous inputs such as `G1`, `1G2`, `1.5M`, `1B`, `1GiB`, and embedded-space forms. Binary 1024-based multipliers are preserved.

### Documentation and configuration

- Updated `README.md` to document current duplicate-destination rejection, strict size syntax, full `delete_min_age` validation, collision-resistant delete-list filenames, and exact trash/hard-delete cleanup behavior.
- Updated `commented_code_map.md` for the modified config, parsing, deletion, upload, cleanup, and result-tracking functions, including the new trash-activity helpers.
- The config schema gained no new options and removed none. Both packaged config examples already contain the complete active option set and were revalidated unchanged.

### Tests and verification

- Expanded the automated suite from 17 to 24 tests.
- Added regression coverage proving the two example legacy-colliding remote paths generate distinct delete-list filename components.
- Added mixed-mode deletion coverage proving hard and trash files stay in separate commands and trash cleanup is enabled only by successful trash-mode deletion.
- Added coverage proving a hard-delete-only plan does not run `rclone cleanup` even when the upload-level default is trash.
- Added coverage proving an actually started trash-mode `sync` is remembered for post-upload trash cleanup.
- Added `--validate-config` coverage for invalid `delete_min_age`, malformed size values, and duplicate upload destinations.
- Updated planning tests to use the new strict size syntax while preserving the same reservation/oldest-file logic.
- Re-ran compile, CLI/version/help, both packaged config validations, full unit suite, fake-rclone end-to-end integration, checksum, documentation-map, and clean-archive checks before packaging.

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
- Added a concurrent single-flight local-size cache keyed by overridden local path plus source-selection filter options.
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
