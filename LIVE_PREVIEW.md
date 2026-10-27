# Independent Home Assistant live preview

Set root `live.enabled` to true to observe rclone execution in HA. This works with `completion.enabled` either true or false. Broker credentials, TLS settings, client ID, policy and topics belong to this channel independently; completion settings are never inherited implicitly.

The script starts the monitor after acquiring its existing exclusive job lock. Each worker updates local memory; the supplied template coalesces updates, publishes heartbeats and refreshes discovery through timeout-bounded subprocesses. It cannot change the rclone result or its phase order.

## Rclone measurements

- `phase`: `reservation_and_upload`, `post_upload_cleanup`, `final_verification`, then `finished`. The first phase includes the existing preparation/trash/upload barriers.
- `current_item`: the remote most recently starting an upload or recording a stage outcome. Several remotes may be active concurrently; this is not an exhaustive list of active work. It clears at completion.
- `completed` / `total`: settled remote stages / four stages per configured remote. Stages are reservation, upload, post-cleanup, and final-quota verification. Success, failure and skipped stages all count as settled. This measures stage completion, not bytes transferred or successful uploads. A failed job can reach 100% when all stages are settled.
- `successful_datasets`, `failed_datasets`, `skipped_datasets`: the template's dataset fields describe rclone remote jobs here, with local `source`, remote `destination`, friendly `name`, and an optional `reason`. A remote is successful only after all four stages succeed. Failure takes precedence; skipped classification appears once its remaining stages settle. Their corresponding counts are actual list lengths.
- `transferred_bytes` / `bytes_per_second`: null, because this adapter does not parse measured rclone transfer statistics. Planned bytes are not presented as actual transfers.
- `stderr`: the last 4,000 characters of captured diagnostics, including upload output (the existing upload runner merges stdout/stderr). Captured output from other commands is appended when those commands return. JSON file listings are not treated as stderr.
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
