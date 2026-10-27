# Syncerate template reporting for rclone-multithreaded-upload

Version 0.0.30 uses the newly supplied template: completion module 1.1.0 and live-preview module 1.2.0. The completion and live modules are copied without changes into the Python package; `mqtt.py` and `reporting.py` adapt rclone results to them.

## Configure the channels independently

The root JSON uses exactly the template's `completion` and `live` section names and option names, alongside the existing rclone settings. Both default to disabled. Start with `config.example.json` or `rclone-cctv-config.example.json`, set your paths/remotes and broker details, then enable the channels you want.

| Desired behavior | `completion.enabled` | `live.enabled` |
| --- | --- | --- |
| No MQTT | `false` | `false` |
| Normal final success/failure notifications | `true` | `false` |
| HA live preview only | `false` | `true` |
| Both | `true` | `true` |

Install the optional dependency in the Python environment running the script:

```bash
python3 -m pip install -r requirements-mqtt.txt
python3 rclone-multithreaded-upload.py --config /path/to/config.json --validate-config
python3 rclone-multithreaded-upload.py --config /path/to/config.json
```

Validation loads and displays both configurations without starting either publisher, creating a lock, or running rclone. The normal command performs the configured upload and cleanup job. HA live preview observes that real execution; enabling it does not make the job a dry run.

## Completion config

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Publish the final job result. |
| `host` | `""` | Hostname/IP; required when enabled. No URL scheme. |
| `port` | `1883` | Integer from 1 to 65535. Set 8883 explicitly if required by your TLS broker. |
| `topic` | `"homeassistant/jobs/status"` | Concrete publish topic without wildcards. The rclone examples use `homeassistant/rclone-upload/result`. |
| `username`, `password` | `null` | Optional authentication. A nonempty password requires a username. |
| `client_id` | `""` | Optional client identifier. Use different identifiers for the two channels if setting them manually. |
| `qos` | `0` | Integer 0, 1, or 2. |
| `timeout` | `15` | Finite positive seconds; covers the whole isolated publish worker, including DNS/connect. |
| `on_success` | `true` | Set false to send only failures. Does not control live preview. |
| `send_on_dry_run` | `true` | Template policy for actual dry runs. This application currently has no job dry-run mode. |
| `tls` | `false` | Verified TLS. |
| `ca_certs`, `certfile`, `keyfile` | `null` | Optional CA and client certificate/key paths. Certificate and key must be supplied together and require TLS. Relative paths resolve beside the JSON config. |

Unknown completion/live keys and invalid JSON types are rejected even when the channel is disabled. Completion events always use `retain=false`, as required by the template. They have no retry queue. Delivery errors are sanitized and do not change the job's exit code. Disabled channels do not import Paho or launch MQTT workers.

## Completion JSON contract

Every completion payload has these template fields:

| Field | Rclone mapping |
| --- | --- |
| `status`, `success`, `exit_code` | `"success"`, `true`, integer 0; or `"failure"`, `false`, a nonzero integer. |
| `title`, `name` | Root `script_name`. |
| `job`, `command` | `"rclone-multithreaded-upload"`. Command is an application identifier, not a dump of CLI arguments. |
| `dry_run` | `false` for real jobs. `--validate-config` emits no report. |
| `error` | String; empty on success, otherwise combined stage/runtime failures. |
| `stderr` | String, last 4,000 characters of captured command diagnostics. Uploads already merge stdout/stderr, so their output is included here. |
| `warning` | Boolean; true if any remote's overall result was skipped. Warnings do not rewrite the result code. |
| `schema_version` | Integer 1. |
| `comment` | Optional root `comment` string, default empty. |
| `version` | Application version, currently `"0.0.30"`. |
| `timestamp`, `host`, `run_id` | UTC report timestamp, local hostname, unique run identifier. Both enabled channels share the run ID. |

Rclone extensions remain available: `event`, `script_name`, `application`, `application_version`, `failed_remotes`, and `remotes`. Each remote retains its name, path, status, four stage statuses, and structured stage errors. Generic completion events omit Syncerate dataset sections; rclone diagnostics use `remotes`. See `payload-examples.json` for full success and failure examples.

Reports are attempted after normal job completion, unexpected runtime exceptions, SIGINT and SIGTERM once the configuration is valid and the exclusive lock has been acquired. Configuration errors and an already-existing lock do not start a reporting session. The lock stays held until final reporting finishes. Completion publication has a deadline; live progress publishes in a separate background worker.

## Existing `mqtt` configs

The former root `mqtt` section is accepted as a completion-only alias. Do not specify both `mqtt` and `completion`. Migration:

- Rename `mqtt` to `completion` and `publish_timeout` to `timeout`.
- Remove `keepalive`; the template's Paho publishing helper manages it. The legacy adapter still validates and accepts the old setting, then ignores it.
- Remove `retain` and `tls_insecure`. Legacy values of false are accepted; true is rejected because the template requires non-retained completion events and verified TLS.
- Replace `client_id: null` with `client_id: ""` and an unused `host: null` with `host: ""` when using the canonical section.
- Legacy configs keep their implicit QoS 1 and former topic; canonical `completion` defaults follow the template (QoS 0 and `homeassistant/jobs/status`). Explicit example topics are unchanged.

The success `error` value is now the template's empty string instead of the former null. Consumer code should read the standard `name`/`title` fields; `script_name` remains an alias. Set `live.enabled` independently if you also want a dashboard.

## Home Assistant examples

`home-assistant-mqtt-automation.example.yaml` retains the existing Pushover and persistent notification actions, now checking that the standard status, boolean success and integer exit code agree. It ignores dry-run payloads. Change `notify.pushover` to your actual action, or remove those actions to use persistent notifications alone. Its exact topic must match `completion.topic`. The Pushover branch reuses the latest supplied template formatter: titles are bounded, message length is budgeted for metadata/error and the latest stderr tail, and shortening is indicated. Rclone remote summaries are included within that same budget. Persistent HA failure notifications keep the full report error/stderr.

`manufacturer-live-preview-card.yaml` is the automatic Markdown dashboard card. Its manufacturer selector is already `rclone-multithreaded-upload`, matching the rclone adapter default and both JSON examples. It discovers matching devices and reads all available fields from the full status/last-result attributes; no entity list is required. The raw card content is also supplied as `manufacturer-live-preview-card.md`. The role markers allow published entities to be renamed. Run a live-enabled configured job to republish discovery after updating.

`home-assistant-live-dashboard.example.yaml` remains an optional, separate entities/gauge dashboard card. The provided JSON examples and dashboard use device ID `rclone_multithreaded_upload`. Choose distinct live device IDs for distinct configured jobs. See `LIVE_PREVIEW.md` for the live fields and options. The live topics never trigger the completion automation.

No real broker, HA instance, cloud service or production data was used for verification. Offline tests cover the actual publisher subprocesses using fake Paho helpers and execute the real application against a fake rclone executable.

## Checking notification and live setup

The two channels remain independent. For normal success/failure notifications, set `completion.enabled=true`, supply its broker settings and install `requirements-mqtt.txt`. Match `completion.topic` with the exact topic in `home-assistant-mqtt-automation.example.yaml`. The JSON uses `status: success/failure`, a real boolean `success`, integer `exit_code`, and string `error`/`stderr`; live `running` packets do not trigger that completion automation.

For the automatic card, separately set `live.enabled=true`, `live.discovery=true` and the live broker settings. The default manufacturer is the application name `rclone-multithreaded-upload`, while `script_name` is the friendly job/device display name. `live.device_id` continues to distinguish jobs. If you intentionally override `live.manufacturer`, use that same text in the card selector. Existing configs without the new manufacturer option receive the correct application default.

`--validate-config` shows enabled live manufacturer and topic settings but does not contact MQTT or publish discovery. A real configured job sends live discovery/state and final results. No broker/HA credentials were supplied for a live test; verification uses real publisher subprocesses and fake rclone/Paho, including a partial-upload failure.

## Compatibility with the supplied automation template

The current completion module still matches the supplied `mqtt_status.py` byte-for-byte. Reports include the fields used by the supplied HA examples: status, title/name/job, boolean success, integer exit_code, warning, error, stderr and dry_run. The application's completion topic is `homeassistant/rclone-upload/result` in both config examples and its HA automation. Set both ends to the same exact case-sensitive topic if customizing it. A sequencing automation that needs both outcomes also needs `completion.on_success=true`.

The rclone automation preserves its dry-run guard, strict success/failure checks, Pushover and persistent notification actions. It uses the updated supplied Pushover formatter with a 1,024-character message budget and a title below 250 characters. Only the notification copy is shortened; MQTT report fields are unchanged. It sends no next-job commands. The unrelated chain topics/readiness commands discussed in the template package are not imported into this project.

Manufacturer recognition applies to the live Markdown card, independently of the completion event topic. Both files are preset to `rclone-multithreaded-upload`. The card discovers registry devices through manufacturer and reads real full JSON attributes from the status/last-result roles. No per-job entity list is required. See `TEMPLATE_COMPATIBILITY.md` for the comparison findings.
