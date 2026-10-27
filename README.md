# rclone-multithreaded-upload

Version 0.0.31

`rclone-multithreaded-upload` uploads one or more local directories to independent rclone destinations while enforcing configured age, file-count, folder-size, and managed remote-size limits.

The application is deliberately stage-based. Threads inside a stage run independently, but the next stage does not begin until every worker in the current stage has finished.

## ⚠️ Disclaimer / Liability

**Use this script at your own risk.**

The author takes **no responsibility or liability** for any data loss, service disruption, misconfiguration, service outage, missed backups, credential exposure, or other damage that may occur from using this script.

Before running it in production, you **must**:

- Read the entire source code
- Understand exactly what it does (and what it does *not* do)
- Review and adapt it to your own environment
- Test it carefully in a non‑production setup

By using this script, **you accept full responsibility** for its effects.

⚠️ AI-assisted / vibe-coded experimental software. Use at your own risk.

## Disclaimer

This project is AI-assisted / vibe-coded software created as a hobby project. It has not been professionally audited and may contain bugs, unsafe behavior, data-loss issues, security problems, or incorrect assumptions.

You are responsible for reviewing the code, testing it in a safe environment, making backups, and understanding what it does before using it on real data. The author is not responsible for damage, data loss, broken systems, security issues, or other problems caused by using this software.

---

### Remote data and source-file effects

This application can perform destructive operations on configured rclone remotes. Cleanup, age-based deletion, file-count limits, directory-size limits, and total remote-size enforcement can permanently delete remote files to make space for uploads.

If a managed remote contains backup data, incorrect remote paths, cleanup paths, filters, age settings, or size limits can delete valid backup copies and may leave you without a usable online backup. Do not treat a remote managed by this script as your only backup.

Always test with disposable data or a test remote first, run `--validate-config`, review the effective remote paths and cleanup limits, and keep at least one separate working backup that this application cannot delete.

## Start using the app

Use a disposable test remote and source directory for the first run. From the extracted project folder:

```bash
cp config.example.json config.json
```

Edit `config.json` with your actual source directories, rclone destinations, cleanup rules and limits. The examples use placeholder paths/remotes. Use `rclone-cctv-config.example.json` for the CCTV layout. Both examples show every canonical application config option, including all completion and live-preview settings.

If you enable either MQTT channel, install its optional dependency into the Python environment running the script:

```bash
python3 -m pip install -r requirements-mqtt.txt
requirements-test.txt
```

Check configuration syntax and values, then review the printed settings:

```bash
python3 rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Validation does not check remote credentials, broker connectivity or source file contents. It creates no job lock, runs no rclone commands and publishes no MQTT messages. After reviewing and testing your settings, a normal invocation runs the configured work:

```bash
python3 rclone-multithreaded-upload.py --config ./config.json
```

Keep the entry script and the complete `rclone_multithreaded_upload/` package together. JSON does not support comments; the option tables below and linked MQTT guides explain every setting.

## Execution order

```text
1. PRE-UPLOAD PREPARATION
   For every remote in parallel:
   - recursive remote snapshot
   - age cleanup planning
   - cleanup_rules max_files/max_size planning
   - filtered exact local source snapshotting and whole-file cap selection when max_total_size reservation is enabled
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

## Parallel destination progress

The live card shows the entire run and every destination at the same time. It lists all active workers, each destination's configured step total, completed/running/failed/skipped counts, current operations, full step table and its own latest diagnostics. A faster destination waiting at a global barrier is shown as waiting rather than falsely running.

For example, destinations with 7, 12 and 16 steps produce one run with **35 total steps**. The overall completed count is the sum of their individual completed counts. Steps are settled when successful, failed, skipped or interrupted; reaching 100% does not imply success. The separate job outcome still comes from the existing reservation/upload/cleanup/verification results.

The planned denominator is fixed at initialization from each destination's actual settings:

```text
7 base steps
+ 2 × number of cleanup rules (pre-upload and post-upload rule planning)
+ 3 when managed quota reservation is enabled (local snapshot, reservation, post-quota planning)
+ 2 when empty_trash is enabled (pre/post trash checks)
+ 1 when sleep_after_step is greater than zero (pre-upload wait)
```

The base steps are the three remote snapshots, two combined delete-plan executions, upload and final verification. Commands with no work to do are labelled skipped. Cleanup-rule planning is tracked per path; the actual deletes remain combined by destination/delete mode. Shared cached local snapshots are acquired once per destination logically while retaining the existing single actual source listing.

Use the updated `manufacturer-live-preview-card.yaml`; its manufacturer recognition and automatic role lookup remain unchanged. The card includes detailed destinations beneath the overall job. `--validate-config` prints the planned overall and per-destination totals without executing anything. See [LIVE_PREVIEW.md](LIVE_PREVIEW.md) and [PARALLEL_PROGRESS.md](PARALLEL_PROGRESS.md) for the schema and interpretation. The synthetic `parallel-progress-examples.json` and rendered `parallel-progress-preview.md` show three destinations uploading concurrently.

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

the application takes one exact filtered local file snapshot with `rclone lsjson`. The snapshot records each candidate path, size, and modification time.

The upload budget is:

```text
max_total_size - 1 MiB safety headroom
```

If the filtered source is larger than that budget, complete files are considered newest-first by rclone `ModTime`. Selection is a contiguous newest-first prefix: files are included only while each next complete file fully fits in the remaining byte budget. As soon as the first next file would exceed the remaining budget, the quota cutoff is considered reached and selection stops immediately. The application does **not** scan farther into older files looking for smaller files that might fit. All files at and after the cutoff wait for a later run.

The remote-space reservation calculation is then:

```text
managed remote bytes after planned pre-cleanup
+ selected complete local upload bytes
+ 1 MiB safety headroom
- max_total_size
```

If space must be freed, the oldest complete managed remote files are selected until at least that byte deficit is covered. The selected local paths are frozen for the upload stage and written to a NUL-separated generated list passed with `--files-from0`. This prevents files created after reservation from silently exceeding the reserved amount. If the newest file itself cannot fit within the upload budget, the selected set is empty; the upload stage is recorded as successfully having nothing eligible to transfer and no rclone upload command or upload-stage backend lookup is started before the pipeline continues.

`--max-transfer` and `--cutoff-mode CAUTIOUS` are not used for quota-managed uploads. Hitting rclone's transfer ceiling returns a non-zero result, so transfer limits are not used as a successful file-selection mechanism.

Identical local source/filter combinations share one concurrent local `lsjson` snapshot during the run.

## Requirements

- Python 3.10 or newer.
- `rclone` available in `PATH` for a normal run.
- Configured rclone remotes.
- Read access to every configured local upload path.
- Write permission for the lock-file parent and delete-list directory.
- Remote permissions required by the configured upload, delete, and optional trash-cleanup operations.

The upload/cleanup application uses the Python standard library. Either MQTT channel additionally requires the optional Paho dependency when enabled.

## CLI commands and flags

The executable has four user-facing option groups. `--help` documents all of them:

| Flag | Meaning |
| --- | --- |
| `-h`, `--help` | Print the complete CLI help, including every supported flag, examples, and usage, then exit. A config file is not required. |
| `-c PATH`, `--config PATH` | Path to the JSON configuration file. Required for a normal run and for `--validate-config`. The filename extension is not enforced; the content must be valid JSON. |
| `--validate-config` | Load and validate the config and print the effective startup summary. Creates no lock, runs no rclone commands and publishes no MQTT. It does not verify remote/broker connectivity or source contents. Requires `-c/--config`. |
| `--version` | Print the application version and exit. A config file is not required. |

Show the complete help:

```bash
./rclone-multithreaded-upload.py --help
```

Show the application version:

```bash
./rclone-multithreaded-upload.py --version
```

Validate a config without touching the lock file or any remote:

```bash
./rclone-multithreaded-upload.py --config ./config.json --validate-config
```

Run normally:

```bash
./rclone-multithreaded-upload.py --config ./config.json
```

The short config form is equivalent:

```bash
./rclone-multithreaded-upload.py -c ./config.json
```

### Invalid or misspelled flags

Any command-line parsing error exits with status `2` and prints the **complete help text first**, including all supported flags and their explanations. This applies to unknown flags, missing required `--config`, and other invalid CLI syntax.

For example:

```bash
./rclone-multithreaded-upload.py --config ./config.json --wrong-flag
```

prints the full help/options list and then an error such as:

```text
error: unrecognized arguments: --wrong-flag
```

## Exit behavior

- `0`: a normal job completed successfully, config validation passed, or an existing lock caused a skip.
- `1`: config loading failed, a required runtime stage failed, or an unexpected job error occurred.
- `2`: command-line parsing failed; complete help is printed.
- `--help` and `--version`: exit with `0` without requiring a config file.
- Existing lock file: prints `Lock file exists, exiting.` and exits with `0` without starting work or reporting a new job.
- SIGINT/SIGTERM: the application finalizes enabled reporting channels, removes its lock, and exits with `128 + signal number`.

## Configuration

Start from `config.example.json` or the CCTV-oriented `rclone-cctv-config.example.json`.

### Root options

| Option | Required | Meaning |
| --- | --- | --- |
| `script_name` | No | Friendly name for this configured job/run. It is printed in the startup summary and included as `script_name` in the MQTT result payload. Default `rclone-multithreaded-upload`. |
| `completion` | No | Syncerate-template final success/failure MQTT reports. Disabled by default. |
| `live` | No | Independent HA live progress, heartbeat, discovery and retained last result. Disabled by default. |
| `mqtt` | No | Legacy completion-only alias; see `MQTT.md` for migration. Do not combine with `completion`. |
| `comment` | No | Optional string included in the completion report. |
| `delete_min_age` | No | Rclone-style age/duration used by cleanup rules that have `delete_old_files=true`. Default runtime value is `31d`. The value is fully parsed during config loading/`--validate-config`; invalid durations or timestamps are rejected before execution. |
| `lock_file` | No | Single-instance lock-file path. |
| `delete_list_dir` | No | Directory used for generated delete lists and quota-managed `--files-from0` upload lists. |
| `sleep_after_step` | No | Seconds each eligible remote waits after pre-upload trash cleanup before the upload barrier can complete. Non-negative integer. |
| `thread_limits` | No | Object containing the four worker limits below. |
| `upload_directories` | Yes | Non-empty list of upload destinations. |

### MQTT completion and independent HA live preview

The application uses the supplied completion module 1.1.0 and live-preview module 1.2.0 with an optional JSON `details` extension for parallel destination progress. Full rclone configs in `config.example.json` and `rclone-cctv-config.example.json` use the template's exact `completion` and `live` section names and keys. Both are disabled by default.

| Setup | `completion.enabled` | `live.enabled` |
| --- | --- | --- |
| Final success/failure notifications | `true` | `false` |
| HA dashboard only | `false` | `true` |
| Both | `true` | `true` |
| No MQTT | `false` | `false` |

Set your broker host, credentials and topics separately in each enabled section. Install the optional dependency with `python3 -m pip install -r requirements-mqtt.txt`. Check the complete config with `python3 rclone-multithreaded-upload.py --config config.json --validate-config`; validation never publishes.

Completion JSON includes all template fields: `status`, boolean `success`, `title`, `name`, `job`, boolean `dry_run`, integer `exit_code`, string `error`/`stderr`, boolean `warning`, and schema/command/comment/version/timestamp/host/run metadata. The name/title come from `script_name`, and job/command identify `rclone-multithreaded-upload`. The existing `script_name`, `failed_remotes`, per-remote stages/errors and application metadata remain as extensions. A successful report has `error: ""`.

`completion.on_success=false` suppresses successful completion events while keeping failure reports; it has no effect on live preview. Completion events are non-retained. Live state is on its own topics, with retained discovery and a separate retained last result. Live preview observes a real job and does not enable a dry-run mode. MQTT transport failures preserve the rclone/cleanup exit code.

See [MQTT.md](MQTT.md) for every config key, payload mappings, old `mqtt` config migration and HA notification setup. See [LIVE_PREVIEW.md](LIVE_PREVIEW.md) for live settings, stage-based progress and dashboard setup. The HA device name comes from `script_name`; with the example device ID the status entity is `sensor.job_live_rclone_multithreaded_upload_status`. Full JSON examples are in `payload-examples.json` and `live-payload-examples.json`.

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
| `remote_path` | Yes | Rclone destination root, for example `EncryptedDrive:CCTV`. The exact same `remote_path` may not appear twice: duplicate destinations produce a safety warning and the config is rejected before any remote work starts. |
| `upload_command` | No | `copy`, `sync`, or `move`. Default `copy`. |
| `delete_old_files` | No | Default age-deletion behavior inherited by cleanup rules that set their override to `null`. Default `true`. |
| `delete_excess_files` | No | Default limit/quota-deletion behavior inherited by cleanup rules. Default `true`. |
| `max_total_size` | No | Maximum total size of the union of files covered by this destination's `cleanup_rules`; `null` disables the remote-wide size cap. Uses the strict size syntax described below. |
| `delete_to_trash` | No | Default delete mode inherited by cleanup rules. `false` requests direct/hard deletion where the backend has a supported rclone flag. |
| `empty_trash` | No | Allows `rclone cleanup` after script-managed trash activity. It does not by itself force trash cleanup. Planned hard-delete-only work is never treated as trash use, even when the upload-level `delete_to_trash` default is `true`. Default `true`. |
| `buffer_size` | No | Per-upload rclone `--buffer-size`, for example `64M`; `null` leaves it unset. Uses the strict size syntax described below. |
| `cleanup_rules` | No | List of relative managed paths and limits owned by this upload destination. |
| `copy_options` | No | Extra rclone options appended to `copy`, `sync`, or `move`, subject to the restrictions below. |

### `cleanup_rules[]`

Each cleanup rule belongs to exactly one upload destination and its `path` is relative to that destination's `remote_path`.

| Option | Required | Meaning |
| --- | --- | --- |
| `path` | Yes | Relative managed path. `/` means the complete upload root. |
| `max_files` | No | Positive maximum file count; `null` disables this limit. |
| `max_size` | No | Maximum size such as `50G`; `null` disables this limit. Uses the strict size syntax described below. |
| `delete_old_files` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |
| `delete_excess_files` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |
| `delete_to_trash` | No | `true`/`false` overrides the upload-level setting; `null` inherits it. |

Duplicate normalized cleanup-rule paths inside the same upload destination are rejected.

The exact same upload `remote_path` is also rejected. The error is deliberately reported as a **WARNING** and the application refuses to continue, because per-remote runtime state and destructive workers are keyed by that destination.

### Strict size syntax

Dedicated size settings parsed by the application (`max_total_size`, cleanup-rule `max_size`, and `buffer_size`) must be a whole number immediately followed by one of:

```text
K   KB
M   MB
G   GB
T   TB
```

Examples: `1K`, `64MB`, `500G`, `1TB`. The units use binary multipliers (1024, 1024², and so on). Leading/trailing whitespace and lowercase unit letters are accepted after normalization, but malformed or ambiguous values are rejected. Values such as `G1`, `1G2`, `1.5M`, `1B`, `1GiB`, or `1 MB` are invalid.

### `delete_min_age` validation

`delete_min_age` is validated during config loading, including `--validate-config`, using the same parser used by cleanup planning. Supported forms include `off`, numeric seconds, fixed single-suffix values such as `31d`, `2w`, `1M`, or `1y`, Go-style time chains such as `2h45m`, and supported ISO/date timestamps. Invalid text is rejected before the lock file or any rclone command is started.

`max_total_size` only covers files included by that destination's `cleanup_rules`. If `cleanup_rules` is empty, the managed union is empty and `max_total_size` has no managed files to count.

## `copy_options` and local-source filters

The upload command receives `copy_options` as configured.

When quota reservation is required, only source-selection options that affect which local files are included are forwarded to the local `rclone lsjson` snapshot:

```text
--min-age
--max-age
--min-size
--max-size
--include
--include-from
--exclude
--exclude-from
--exclude-if-present
--filter
--filter-from
--files-from
--files-from-raw
--files-from0
--hash-filter
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

Use the dedicated config options for the behavior the application manages itself. In addition, quota-managed `sync` rejects `--delete-excluded` because files skipped by the byte budget are intentionally excluded and must not be deleted from the destination.

## Delete modes

When `delete_to_trash=true`, no hard-delete flag is added and the backend's normal trash behavior is used.

When `delete_to_trash=false`, the application resolves wrapper remotes such as `crypt` through `rclone config dump` and adds a verified backend-specific direct-delete flag when available:

```text
Google Drive -> --drive-use-trash=false
MEGA         -> --mega-hard-delete
OneDrive     -> --onedrive-hard-delete
```

Other backend types receive no backend-specific hard-delete flag from this application.

Trash and hard-delete decisions are preserved at the level where they are configured:

- upload-level `delete_to_trash` is the default inherited by cleanup rules that use `null`;
- a cleanup rule with an explicit `true` or `false` keeps that override in the combined delete plan;
- mixed plans are split into separate trash-mode and hard-delete `rclone delete --files-from` commands;
- a successful hard-delete command is never recorded as trash activity;
- a successful trash-mode planned delete is recorded for that exact reservation/post-cleanup stage;
- an actually started `rclone sync` with upload-level `delete_to_trash=true` is recorded as possible script-managed trash activity because sync can delete destination-only files.

`empty_trash=true` permits `rclone cleanup` only when the relevant completed stage recorded script-managed trash activity. Therefore an upload whose default is `delete_to_trash=true` but whose actual cleanup deletions were all hard-delete overrides does **not** empty unrelated backend trash. Conversely, a cleanup rule that explicitly uses trash still enables trash cleanup even if the upload-level default is hard delete. `empty_trash=false` always skips `rclone cleanup`.

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

### Local quota-managed upload snapshot

```bash
rclone lsjson LOCAL_PATH --recursive --files-only --no-mimetype [source-selection filters]
```

Used only when `delete_excess_files=true` and `max_total_size` is configured for that upload destination. The exact file list is reused for candidate sizing, newest-first whole-file cap selection, reservation, and the generated upload list.

### Planned deletion

```bash
rclone delete --files-from DELETE_LIST REMOTE: [delete-mode options]
```

Executes the combined delete plan generated from the in-memory snapshot.

### Trash cleanup

```bash
rclone cleanup REMOTE:
```

Runs only when `empty_trash=true` and the relevant stage recorded script-managed trash activity: a successful trash-mode planned deletion, or for the post-upload stage an actually started trash-mode `sync`. Hard-delete-only work does not trigger this command.

### Upload

One of:

```bash
rclone copy LOCAL_PATH REMOTE: [options]
rclone sync LOCAL_PATH REMOTE: [options]
rclone move LOCAL_PATH REMOTE: [options]
```

| Upload command | Effect |
| --- | --- |
| `copy` | Copy selected local files to the destination; the upload itself does not remove the source or prune unrelated destination files. Configured cleanup phases still apply. |
| `sync` | Make the destination match the selected source using rclone sync behavior, including destination deletions where applicable. |
| `move` | Transfer selected local files and allow rclone to remove successfully moved source files. |

For quota-managed uploads, source-selection filters are first applied to the frozen local snapshot, then replaced on the transfer command by the exact generated `--files-from0` list. Non-filter options such as stats and transfer concurrency remain in place.

`sync` can delete destination files that are absent from the selected source according to normal rclone sync semantics. For quota-managed `sync`, `--delete-excluded` is rejected during config validation because over-budget files are intentionally excluded from the generated list and must not be deleted from the destination merely because they were skipped for that run.

## Generated files and lock behavior

The configured lock file prevents two normal instances using that lock path from running at once. The file contains the process PID and is removed on normal process exit through the registered cleanup handler, and also on handled SIGINT/SIGTERM.

Combined delete plans are written below `delete_list_dir` with names derived from the phase, delete mode, and a readable remote-name fragment plus the full SHA-256 of the original `remote_path`. The hash prevents legacy filename collisions such as `a:b/c` versus `a_b:c`. The generated delete lists are passed to `rclone delete --files-from`.

Quota-managed upload selections also use `delete_list_dir` for a collision-resistant `to-upload-...files0` file. It is NUL-separated and passed to rclone with `--files-from0`, so paths containing whitespace, `#`, or `;` are not misparsed as line-oriented filter syntax.

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

## MQTT final result and Home Assistant

Final completion reporting is attempted after runtime success/failure, unexpected exceptions and handled interrupts once a valid configuration and exclusive job lock are available. Existing-lock exits and `--validate-config` start neither channel. Reporting finishes before this invocation releases its lock.

`home-assistant-mqtt-automation.example.yaml` retains Pushover and persistent notifications and checks consistency of the template's status, boolean success and integer exit code. Replace `notify.pushover` with your configured action, or remove those actions. Match its exact topic to `completion.topic`. Pushover titles/messages use the supplied template's bounded formatting, with metadata and the latest stderr tail. If shortened, the message says so; the full completion payload and the persistent HA failure notification retain the complete report diagnostics.

`home-assistant-live-dashboard.example.yaml` is an independent card for the live device. Progress counts settled detailed workflow steps across all destinations, with an independent step list and counts for each destination; byte totals and speed remain unknown. Set its entity IDs to match `live.device_id`. The live topics are separate from completion notifications.

For a card that fills its fields and adds matching jobs automatically, paste `manufacturer-live-preview-card.yaml` as a manual Markdown card. Both it and the raw `manufacturer-live-preview-card.md` already select manufacturer `rclone-multithreaded-upload`. No entity IDs need to be edited. The live config defaults to the same manufacturer, including when upgrading an existing config that omits this new field. Run a configured job with live publishing and discovery enabled so HA receives the updated discovery definitions and full state attributes. The per-job device name still comes from `script_name`.

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
    mqtt.py
    mqtt_status.py
    ha_live_preview.py
    reporting.py
    output.py
    phases.py
    planning.py
    progress.py
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
    test_mqtt_status.py
    test_ha_live_preview.py
    test_reporting_adapter.py
    test_manufacturer_card.py
    test_parallel_progress.py
config.example.json
rclone-cctv-config.example.json
home-assistant-mqtt-automation.example.yaml
home-assistant-live-dashboard.example.yaml
manufacturer-live-preview-card.yaml
asus-frigate-live-preview-card.yaml
manufacturer-live-preview-card.md
ha-discovery-examples.json
MQTT.md
LIVE_PREVIEW.md
payload-examples.json
live-payload-examples.json
requirements-mqtt.txt
requirements-test.txt
README.md
VERSIONING.md
commented_code_map.md
verification_report.txt
TEMPLATE_COMPATIBILITY.md
PARALLEL_PROGRESS.md
parallel-progress-examples.json
parallel-progress-preview.md
SHA256SUMS
RELEASE_MANIFEST.json
AGENTS.md
```

The optional `asus-frigate-live-preview-card.yaml` is the same detailed card preset to manufacturer `rclone-multithreaded-upload-ubuntu-zfs-raid-import-test`, matching the custom configuration discussed with this project. Use the normal manufacturer card for the default script manufacturer. Upgrade both the application package and the card to receive and display detailed destination progress.

For internal function-by-function behavior, see `commented_code_map.md`. For release history, see `VERSIONING.md`.

Each destination also exposes `cleanup_jobs`: an independent view of each configured cleanup path, with its own pre/post planning states and settled/total count (normally two planning steps). These are views of the same steps and are never counted again in the overall total. Actual deletion stays in the existing combined delete-plan operation. The card displays these per-path counts above the full destination step table.
