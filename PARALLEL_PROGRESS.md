# Per-destination progress and whole-run totals

Version 0.0.32 tracks actual execution independently for every configured destination. It keeps the existing four summary results for outcome/safety decisions, and adds detailed observations for display and diagnostics. Reporting does not decide whether an upload or cleanup may run.

## Step totals

Each destination has seven base steps: pre-upload remote snapshot, combined pre-delete execution, upload, post-upload remote snapshot, combined post-delete execution, final remote snapshot and final verification. Each configured cleanup rule adds separate pre/post planning steps. Enabled managed quota adds local snapshot acquisition, space reservation and post-quota planning. `empty_trash=true` adds pre/post trash checks. A positive `sleep_after_step` adds one pre-upload wait.

Thus the denominator is `7 + 2*rules + 3*quota_enabled + 2*empty_trash + (sleep_after_step>0)`. Here `quota_enabled` means `delete_excess_files=true` with a configured `max_total_size`; boolean terms are 0/1. Totals are fixed from the actual config at initialization. No config knob or new public CLI flag is needed.

Example with no configured wait: OneDrive with no rules/quota/trash has 7 steps; GDrive with one rule, quota and no trash has 12; Mega with two rules, quota and trash has 16. The overall run has 35 steps. Each retains its own independent count and percentage. Completed means settled, so failures/skips/interrupts are visible and included; 100% does not imply success.

Per-rule entries describe real planning, not separate delete commands. Actual deletions are still combined by destination and delete mode. A zero-entry delete plan, unused/unsupported trash cleanup, disabled rule or empty quota upload is shown as skipped with a reason. Shared local-source cache acquisition is visible for each destination while the source is actually listed only once, as before.

## Live JSON

The standard live fields retain their types and meanings. `completed`, `total` and `progress_percent` refer to detailed workflow steps; `current_item` summarizes all active destinations, not the last thread to write it. `details` is an application extension containing:

- `schema_version: 1`, `unit: "steps"`.
- `destination_count`, `active_destination_count`, `completed`, `total`, `running`, `failed`, `skipped`.
- `activity_counts`: running-step counts by readable activity (Upload, Cleanup, Trash cleanup, Snapshot, Quota planning, Waiting, Verification). `activity` is a readable whole-run summary of all active categories, or Waiting for next phase / Finished / Preparing. These describe actual observations, independently of the legacy stage barrier.
- `completed_stages`, `total_stages`: the legacy four-stage accounting for existing consumers.
- `remote_jobs`: one record per configured destination, including `name`, `source`, `destination`, `status`, `completed`, `total`, `progress_percent`, `running`, `succeeded`, `failed`, `skipped`, `pending`, `active_steps`, `activity`, `activity_counts`, `steps`, legacy `stages`, `error`, `has_failures`, and a bounded independent `stderr` tail.

Every step has a stable `id`, readable `label`, owning legacy `stage`, operation `kind`, readable `activity` (trash is distinguished from ordinary cleanup), `status`, `error`/reason, `started_at` and `finished_at`. States are `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, `SKIPPED`, `INTERRUPTED`. Unstarted skipped steps have no start timestamp. Failed/interrupted observations never become later successes. The `failed` counter includes interrupted steps; the explicit step status distinguishes them.

Destination status is pending/waiting/running while work or later phases remain. Terminal status derives from real stage and step outcomes. A destination can wait after a failure while its permitted post-upload cleanup is still pending; `has_failures`, stage error and failed steps remain visible. Errors between observed operations belong to the destination/stage; an unstarted step is not falsely marked as having run. On shutdown, steps that were not reached become skipped with a reason, and interrupted active work remains interrupted. No unresolved observation is turned into an invented success.

Updates snapshot the entire set under locks and merge standard counters/details atomically. Thread-local context routes diagnostics to the actual destination. Network work still runs through the template's coalescing, timeout-bounded background publisher. A last-result packet includes the final full details.

## Completion and HA

Completion JSON stays compatible with the existing automation. Its standard status/success/integer exit code are authoritative job results. Non-conflicting top-level `progress` totals and `remotes[].progress` snapshots preserve detailed final observations without replacing the original remote stages/errors.

The manufacturer Markdown/YAML card automatically reads `details.remote_jobs` from the run device's aggregate status/last-result attributes. It shows overall progress and the combined active-operation summary first, then a compact dynamic table with one row per configured destination (name, current activity/step, settled/total, percentage, state). It then shows every destination's active steps, complete step table, progress and diagnostics. Adding/removing entries in `upload_directories` changes rows automatically; destination names are not hardcoded in the card. Set each entry's `name` to a label such as OneDrive, GDrive or Mega; omitted names fall back to `remote_path`. Failed steps remain flagged even while permitted later work continues. Manufacturer matching and role markers remain unchanged; renamed entities remain supported. Generic jobs without `details` still render with the original item layout.

## Practical limits

These are workflow-step counts, not transfer-byte percentages or time estimates. A long upload remains one active step until its command returns. Byte totals/speed stay Unknown because actual transfer statistics are not parsed. Fast intermediate operations can be coalesced into a later MQTT packet, but their recorded states/timestamps remain in the complete step table.

The existing thread limits and stage barriers still control concurrency. The monitor reports all workers actually running in the current phase; it does not schedule work or make previously sequential stages overlap. `--validate-config` prints the planned overall and per-destination counts without creating a lock, running rclone or publishing MQTT.

`parallel-progress-examples.json` and `parallel-progress-preview.md` use synthetic, internally consistent three-destination progress generated from the real tracker. They contain no broker credentials and do not represent a production job.

Each destination also exposes `cleanup_jobs`: an independent view of each configured cleanup path, with its own pre/post planning states and settled/total count (normally two planning steps). These are views of the same steps and are never counted again in the overall total. Actual deletion stays in the existing combined delete-plan operation. The card displays these per-path counts above the full destination step table.

The example preview includes all three uploads running and a separate synthetic snapshot with upload, pre-upload deletion and pre-upload trash cleanup active on different destinations. Older detailed payloads without activity metadata still render using active step labels and the original phase fallback.
