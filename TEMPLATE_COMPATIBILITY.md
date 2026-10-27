# Supplied MQTT template compatibility

Application: rclone-multithreaded-upload 0.0.30.
Supplied archive: syncerate-mqtt-template.zip.
Archive SHA-256: `1558fed2b1cd59b2cfd5b31df0996f3d3779259700b2b7e524a14d91a7de1a43`.

The supplied archive updates completion-automation guidance and adds a bounded Pushover notification example. Its completion 1.1.0 module, live 1.2.0 module and manufacturer card logic match the application's integrated versions.

| Part | Result |
| --- | --- |
| Completion JSON | `mqtt_status.py` matches byte-for-byte. Rclone reports keep the standard typed fields and existing per-remote extensions. |
| HA automation | The combined rclone automation uses the current formatter for bounded Pushover title/message, latest stderr tail and a shortening marker. It keeps the dry-run guard, strict result checks and existing full persistent failure notification. Bounded rclone remote summaries are included in the same message budget. |
| Live publisher | `ha_live_preview.py` matches byte-for-byte, including manufacturer in discovery/state and full aggregate attributes with stable role markers. |
| Manufacturer selector | Default `live.manufacturer` and both card selectors are `rclone-multithreaded-upload`. Root `script_name` remains the individual job/device display name. |
| Markdown/YAML | Both match the supplied card logic; the only substitutions are the rclone manufacturer/comment and remote labels. YAML wraps the exact raw Markdown content. |
| Automatic recognition | Devices are filtered by HA manufacturer. Status and last-result fields are found through `live_preview_role`; renamed entities and new matching jobs are covered by rendering tests. |
| Independence | Completion and live channels retain separate enable switches, credentials, topics and workers. The Markdown card sends no notifications or next-job commands. |
| Safety and identity | Upload/cleanup/quota/lock/barrier logic, discovery IDs and topic behavior are unchanged. |

For HA installation, match your completion topic and configured `notify.pushover` action. Paste `manufacturer-live-preview-card.yaml` as a manual Markdown card. Enable configured live publishing/discovery and run your reviewed job so HA receives its retained definitions and state. Existing configs that omit manufacturer receive the rclone application default. A custom manufacturer must use identical text in the card selector.

The supplied template also discusses another project's sequencing automation. Those instructions/topics are reference data and are not applied to rclone. This update does not alter scheduling, external listener commands or continue-after-failure policy.

The full offline suite and package verification results are recorded in `verification_report.txt`. No real HA instance, broker or Pushover service was contacted. Transfer bytes/speed remain Unknown unless actually measured by an adapter.
