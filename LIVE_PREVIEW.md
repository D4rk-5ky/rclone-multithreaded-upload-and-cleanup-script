# Independent Home Assistant live preview

Set root `live.enabled` to true to observe rclone execution in HA. This works with `completion.enabled` either true or false. Broker credentials, TLS settings, client ID, policy and topics belong to this channel independently; completion settings are never inherited implicitly.

The script starts the monitor after acquiring its existing exclusive job lock. Detailed per-destination observations are initialized from the configuration before any worker runs. Each worker updates local memory; the supplied template coalesces updates, publishes heartbeats and refreshes discovery through timeout-bounded subprocesses. It cannot change the rclone result or its phase order.

## Automatic Markdown dashboard

Paste the complete `manufacturer-live-preview-card.yaml` into a manual Markdown card in HA. Alternatively paste only `manufacturer-live-preview-card.md` into a Markdown card's Content field. Both files already contain:

```jinja
{% set manufacturer = 'rclone-multithreaded-upload' %}
```

The card finds devices whose HA manufacturer matches this application, discovers their status and last-result entities through the `live_preview_role` attributes, and fills phase/current item, counters/progress, runtime, outcomes, error and stderr automatically. No per-job entity IDs are configured. The JSON dataset arrays contain rclone remote outcomes and are displayed as remotes. Jobs added later appear automatically, and renamed entities remain supported after receiving discovery/state.

Set `live.enabled=true` and `live.discovery=true`, configure the live broker and install `requirements-mqtt.txt`. Run your already reviewed job configuration once with the updated code so retained discovery definitions are refreshed. Both config examples include the matching manufacturer; an existing config that omits it also receives this application default. The friendly device name remains root `script_name`, and the stable `device_id` and discovery unique IDs are unchanged. A different manufacturer override requires changing the card selector too.

The updated template publishes the full JSON state as attributes on the status and retained-result sensors, tagged `live_preview_role: status` / `last_result`. This makes every available field discoverable. A newer run whose heartbeat expires is shown as unknown rather than replaced by an older success. Missing measured bytes/speed remain Unknown; the card does not invent measurements.

`home-assistant-live-dashboard.example.yaml` is preserved as an optional entities/gauge layout for a specific device. The manufacturer card is the automatic alternative. `ha-discovery-examples.json` shows the actual rclone discovery definitions.

## Rclone measurements

- `phase`: `reservation_and_upload`, `post_upload_cleanup`, `final_verification`, then `finished`. The first phase includes the existing preparation/trash/upload barriers.
- `current_item`: a summary of all currently active destinations and operation labels. The complete list is also present in `details.remote_jobs`; it is not replaced by the last worker to report. It clears at completion.
- `completed` / `total`: settled detailed workflow steps / the sum of each destination's planned steps. Totals depend on cleanup-rule count, quota settings, trash setting and delay. Success, failure, skipped and interrupted observations count as settled. This measures workflow progress, not bytes or successful uploads. A failed job can reach 100% when all steps are settled. The four legacy stage counters remain available as `details.completed_stages` / `details.total_stages`.
- `successful_datasets`, `failed_datasets`, `skipped_datasets`: the template's dataset fields describe rclone remote jobs here, with local `source`, remote `destination`, friendly `name`, and an optional `reason`. A remote is successful only after all four stages succeed. Failure takes precedence; skipped classification appears once its remaining stages settle. Their corresponding counts are actual list lengths.
- `transferred_bytes` / `bytes_per_second`: null, because this adapter does not parse measured rclone transfer statistics. Planned bytes are not presented as actual transfers.
- `stderr`: the last 4,000 characters of captured diagnostics from all workers, including merged upload output. Each destination also has its own separate 4,000-character tail in `details.remote_jobs[].stderr`, routed through the actual worker context. Captured stderr from other commands is appended when those commands return. JSON file listings are not treated as stderr.
- `runtime_seconds`, `last_heartbeat`, `started_at`, `finished_at`: supplied by the live template. Runtime freezes at finish. Both channels share `name`, `job`, version, and run ID.

Configured MQTT passwords and common rclone credential flags are redacted from reported diagnostics. Existing console output behavior is unchanged.

## Live config

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enable this channel independently. |
| `host`, `port` | `""`, `1883` | Hostname/IP and integer port; host required when enabled. |
| `username`, `password` | `null` | Independent optional broker credentials. |
| `client_id` | `""` | Empty derives an ID from the device ID. Use a distinct value from completion if configured. |
| `qos` | `1` | Integer 0, 1 or 2. |
| `timeout` | `5` | Positive finite seconds per publisher subprocess. |
| `tls` | `false` | Verified TLS. |
| `ca_certs`, `certfile`, `keyfile` | `null` | Optional TLS paths, resolved beside the JSON config; same validation as completion. |
| `manufacturer` | `"rclone-multithreaded-upload"` | Application group published in discovery and state. The automatic card selects this exact value. Custom overrides must match the card selector. |
| `device_id` | Derived | Lowercase letters, numbers and underscores, 1–80 characters. If omitted, derived stably from hostname, application and absolute config path. Examples explicitly use `rclone_multithreaded_upload`. |
| `topic_prefix` | `"job-live"` | State and retained-result topic prefix. Rclone config examples set `rclone-upload/live`. |
| `discovery` | `true` | Publish retained discovery for 14 sensors. |
| `discovery_prefix` | `"homeassistant"` | Your HA MQTT discovery prefix. |
| `send_on_dry_run` | `false` | Template policy; the current rclone application does not run dry-run jobs. |
| `heartbeat_seconds` | `10` | Positive finite heartbeat interval. |
| `min_publish_interval` | `1` | Positive finite minimum interval; must not exceed heartbeat interval. |
| `expire_after` | `45` | Integer seconds; must exceed two heartbeat intervals plus publish timeout. |
| `discovery_interval` | `300` | Positive finite seconds between successful discovery refreshes. |
| `final_wait` | `1` | Positive finite bounded drain wait; can increase for a slower broker. |

With the provided example settings:

- Live state: `rclone-upload/live/rclone_multithreaded_upload/state`, non-retained.
- Last real result: `rclone-upload/live/rclone_multithreaded_upload/last_result`, retained.
- Discovery: `homeassistant/sensor/job_live_rclone_multithreaded_upload_<field>/config`, retained.

Live status uses the template's `running`, `success`, `failure`, `interrupted` or `incomplete` states. Unknown measurements remain JSON null. SIGINT/SIGTERM finalize with nonzero integer codes 130/143 and live status `interrupted`; their completion events use standard `failure`. Lost heartbeats expire to unavailable and do not imply success or failure. Live sensors also expire normally after a finished job; the separate retained last-result sensor holds the last real outcome.

The monitor sends no notifications itself. Enable the completion channel and its separate automation for normal success/failure notifications. See `live-payload-examples.json` and the independent dashboard example.

## Device and entity names

The HA device uses the configured root `script_name` as its display name. The example live device ID is `rclone_multithreaded_upload`, so the requested entity IDs include:

- `sensor.job_live_rclone_multithreaded_upload_status`
- `sensor.job_live_rclone_multithreaded_upload_progress_percent`
- `sensor.job_live_rclone_multithreaded_upload_last_result`

HA may add a suffix if an entity ID already exists. Change the dashboard card to match the actual entity IDs. Discovery must be enabled in the live config and accepted by the connected HA MQTT integration.

## Offline checks

The runtime MQTT dependency remains optional. To run the full offline suite, including actual Markdown/YAML rendering with a simulated HA registry:

```bash
python3 -m pip install -r requirements-test.txt
python3 -m unittest discover -s tests -v
```

Card tests use the real discovery/state templates and include automatic job addition, renamed entities, success/failure results, first runs, heartbeat expiry, unknown metrics and diagnostic escaping. They do not connect to a real HA instance.

## Concurrent destination details

The updated card shows the whole run plus one section per destination: source/remote, waiting/running/outcome state, independent completed/total/percentage, successful/failed/skipped counts, every active operation, and the complete step table with reason/error text. A normal rclone pipeline still enforces its existing global stage barriers. Parallel workers within a stage are all visible; this does not enable uploads to overlap phases that previously waited at barriers.

`details.remote_jobs` carries these snapshots atomically with the standard total/completed fields. It is retained in the live last-result packet and available in full status/result attributes. The template's only local module extension is an optional JSON-object `details` update; all standard fields, discovery identities, manufacturer recognition, timeouts and retention remain intact. Completion events retain their standard JSON and add overall `progress` plus per-remote `progress` extensions.

The total formula, exact statuses, schema and limitations are documented in `PARALLEL_PROGRESS.md`. Replace an older card with the delivered updated YAML to see the per-destination tables. For a custom manufacturer, keep the same exact selector text as `live.manufacturer`.

For the custom manufacturer `rclone-multithreaded-upload-ubuntu-zfs-raid-import-test`, use `asus-frigate-live-preview-card.yaml`, already preset to that exact value. Upgrade the application and card together; older application versions do not emit the detailed destination object.

Each destination also exposes `cleanup_jobs`: an independent view of each configured cleanup path, with its own pre/post planning states and settled/total count (normally two planning steps). These are views of the same steps and are never counted again in the overall total. Actual deletion stays in the existing combined delete-plan operation. The card displays these per-path counts above the full destination step table.
