{# Preset for rclone-multithreaded-upload; fields and jobs populate automatically. #}
{% set manufacturer = 'rclone-multithreaded-upload' %}
{% set dataset_limit = 10 %}
{% set stats = namespace(records=[], seen=[], running=0, finished=0, failed=0, other=0) %}
{% for sensor in states.sensor %}
  {% set dev = device_id(sensor.entity_id) %}
  {% if dev and dev not in stats.seen and device_attr(dev, 'manufacturer') == manufacturer %}
    {% set fields = namespace(live='', last='') %}
    {% for eid in device_entities(dev) %}
      {% set role = state_attr(eid, 'live_preview_role') %}
      {% if role == 'status' %}
        {% set fields.live = eid %}
      {% elif role == 'last_result' %}
        {% set fields.last = eid %}
      {% endif %}
    {% endfor %}
    {# Name fallbacks also discover entities before their first state packet. #}
    {% for eid in device_entities(dev) %}
      {% set label = state_attr(eid, 'friendly_name') | default('', true) | lower %}
      {% if not fields.live and (eid.endswith('_status') or label.endswith(' status') or label == 'status') %}
        {% set fields.live = eid %}
      {% endif %}
      {% if not fields.last and (eid.endswith('_last_result') or label.endswith('last result')) %}
        {% set fields.last = eid %}
      {% endif %}
    {% endfor %}
    {% if fields.live or fields.last %}
      {% set stats.seen = stats.seen + [dev] %}
      {% set live_state = states(fields.live) if fields.live else 'unknown' %}
      {% set last_state = states(fields.last) if fields.last else 'unknown' %}
      {% set live_run = state_attr(fields.live, 'run_id') if fields.live else none %}
      {% set last_run = state_attr(fields.last, 'run_id') if fields.last else none %}
      {% set pending = live_state in ['unknown', 'unavailable'] and fields.live and state_attr(fields.live, 'status') == 'running' and live_run and live_run != last_run %}
      {% if pending %}
        {% set source = fields.live %}
        {% set outcome = 'heartbeat_lost' %}
      {% elif live_state in ['running', 'success', 'failure', 'failed', 'incomplete', 'interrupted'] %}
        {% set source = fields.live %}
        {% set outcome = 'failure' if live_state == 'failed' else live_state %}
      {% elif last_state in ['success', 'failure', 'failed', 'incomplete', 'interrupted'] %}
        {% set source = fields.last %}
        {% set outcome = 'failure' if last_state == 'failed' else last_state %}
      {% else %}
        {% set source = fields.live or fields.last %}
        {% set outcome = 'unknown' %}
      {% endif %}
      {% set name = device_attr(dev, 'name_by_user') or device_attr(dev, 'name') or state_attr(source, 'name') or dev %}
      {% set stats.records = stats.records + [dict(device=dev, source=source, last=fields.last, outcome=outcome, name=name)] %}
      {% if outcome == 'running' %}
        {% set stats.running = stats.running + 1 %}
      {% elif outcome == 'success' %}
        {% set stats.finished = stats.finished + 1 %}
      {% elif outcome == 'failure' %}
        {% set stats.failed = stats.failed + 1 %}
      {% else %}
        {% set stats.other = stats.other + 1 %}
      {% endif %}
    {% endif %}
  {% endif %}
{% endfor %}
## {{ manufacturer | e }} — Job Monitor

| 🟢 Running | ✅ Finished | 🔴 Failed | ⚪ Other / unknown |
|:---:|:---:|:---:|:---:|
| **{{ stats.running }}** | **{{ stats.finished }}** | **{{ stats.failed }}** | **{{ stats.other }}** |

**{{ stats.records | count }} job(s) discovered**

{% for record in stats.records | sort(attribute='name') %}
{% set source = record.source %}
{% set outcome = record.outcome %}
{% set icon = '🟢' if outcome == 'running' else '✅' if outcome == 'success' else '🔴' if outcome == 'failure' else '🟠' if outcome in ['incomplete', 'interrupted', 'heartbeat_lost'] else '⚪' %}
{% set total = state_attr(source, 'total') %}
{% set done = state_attr(source, 'completed') %}
{% set percentage = state_attr(source, 'progress_percent') | float(none) %}
{% set transferred = state_attr(source, 'transferred_bytes') | float(none) %}
{% set secs = state_attr(source, 'runtime_seconds') | float(none) %}
{% set rate = state_attr(source, 'bytes_per_second') | float(none) %}
{% set successful = state_attr(source, 'successful_datasets') or [] %}
{% set failed = state_attr(source, 'failed_datasets') or [] %}
{% set skipped = state_attr(source, 'skipped_datasets') or [] %}
{% set dry_run = state_attr(source, 'dry_run') | default(false, true) | bool %}
{% set error = state_attr(source, 'error') or '' %}
{% set stderr = state_attr(source, 'stderr') or '' %}
{% set detail = state_attr(source, 'details') %}
{% set jobs = detail.remote_jobs if detail is mapping and detail.remote_jobs is defined else [] %}

---

### {{ icon }} {{ record.name | e }} — {{ outcome | replace('_', ' ') | upper }}{% if dry_run %} · PREVIEW{% endif %}

{% if outcome == 'running' %}
**Phase:** {{ detail.activity if jobs and detail.activity is defined else (state_attr(source, 'phase') or 'Preparing') | replace('_', ' ') }}

**Current:** {{ state_attr(source, 'current_item') or 'Preparing next item' }}
{% elif outcome == 'heartbeat_lost' %}
**Heartbeat expired during a newer run. Its outcome is unknown.**
{% if record.last and states(record.last) not in ['unknown', 'unavailable', ''] %}
Previous retained result: {{ states(record.last) | upper }} — it belongs to another run.
{% endif %}
{% elif outcome == 'unknown' %}
**No completed result yet.**
{% else %}
{% set finished_at = state_attr(source, 'finished_at') %}
{% set finished_time = as_datetime(finished_at, none) if finished_at else none %}
**Finished:** {{ as_local(finished_time).strftime('%Y-%m-%d %H:%M') if finished_time else 'Time unavailable' }}

**Exit code:** {{ state_attr(source, 'exit_code') if state_attr(source, 'exit_code') is not none else 'Unknown' }}
{% endif %}

{% if state_attr(source, 'warning') %}
**Warning:** This job reported a warning.
{% endif %}
{% if error %}
**Error:** {{ error | e | replace('\n', ' ') }}
{% endif %}

{% if jobs %}
**Overall run progress**

{% endif %}
{% if percentage is not none %}
{% set percent = [0, [100, percentage] | min] | max %}
{% set bars = (percent // 10) | int %}
`{{ '█' * bars }}{{ '░' * (10 - bars) }}` **{{ percent | round(1) }}%**
{% else %}
**Progress:** Unknown
{% endif %}

{% if jobs %}
**Steps:** {{ done if done is not none else '?' }} / {{ total if total is not none else '?' }} settled

**Destinations:** {{ jobs | count }} · **Active destinations:** {{ detail.active_destination_count }} · **Running steps:** {{ detail.running }} · **Failed steps:** {{ detail.failed }} · **Skipped steps:** {{ detail.skipped }}

**Destinations — live overview**

| Destination | Activity / current step | Settled steps | Progress | State |
|:---|:---|:---:|:---:|:---|
{% for destination in jobs -%}
| {{ destination.name | e | replace('|', '&#124;') | replace('\n', ' ') }} | {{ destination.activity | default('Active' if destination.active_steps else 'Finished' if destination.completed == destination.total else 'Waiting for next phase') | e | replace('|', '&#124;') | replace('\n', ' ') }}{% if destination.active_steps %}: {{ destination.active_steps | join('; ') | e | replace('|', '&#124;') | replace('\n', ' ') }}{% endif %} | {{ destination.completed }} / {{ destination.total }} | {{ destination.progress_percent }}% | {{ destination.status | upper }}{% if destination.has_failures | default(false) %} · ⚠ failure recorded{% endif %} |
{% endfor %}

{% for destination in jobs %}
#### {{ destination.name | e | replace('\n', ' ') }} — {{ destination.status | upper }}

{{ destination.source | e }} → {{ destination.destination | e }}

**Progress:** {{ destination.completed }} / {{ destination.total }} steps settled ({{ destination.progress_percent }}%) · {{ destination.succeeded }} succeeded · {{ destination.failed }} failed · {{ destination.skipped }} skipped

**Active:** {{ destination.active_steps | join('; ') | e if destination.active_steps else 'None; waiting or finished' }}
{% if destination.error | default('') %}

**Destination error:** {{ destination.error | e | replace('\n', ' ') }}
{% endif %}

{% if destination.cleanup_jobs | default([]) %}
**Individual cleanup rules — planning progress**

| Cleanup path | Settled | Pre-upload | Post-upload |
|:---|:---|:---|:---|
{% for cleanup in destination.cleanup_jobs -%}
| {{ cleanup.path | e | replace('|', '&#124;') | replace('\n', ' ') }} | {{ cleanup.completed }} / {{ cleanup.total }} | {{ cleanup.pre_status }} | {{ cleanup.post_status }} |
{% endfor %}
{% endif %}

| Step | Phase | State | Detail |
|:---|:---|:---|:---|
{% for task in destination.steps -%}
| {{ task.label | e | replace('|', '&#124;') | replace('\n', ' ') }} | {{ task.stage | e }} | {{ '🟢' if task.status == 'RUNNING' else '✅' if task.status == 'SUCCESS' else '🔴' if task.status == 'FAILED' else '🟠' if task.status == 'INTERRUPTED' else '⏭' if task.status == 'SKIPPED' else '⏳' }} {{ task.status }} | {{ task.error[:200] | e | replace('|', '&#124;') | replace('\n', ' ') }} |
{% endfor %}
{% if destination.stderr %}

**Latest output for {{ destination.name | e }}**

<pre>{{ destination.stderr | e }}</pre>
{% endif %}
{% endfor %}
{% else %}
**Items:** {{ done if done is not none else '?' }} / {{ total if total is not none else '?' }} completed
{% endif %}

{% if successful or failed or skipped %}
**Remotes:** {{ successful | count }} successful · {{ failed | count }} failed · {{ skipped | count }} skipped
{% endif %}

**Transferred:** {% if transferred is not none %}{{ '%.2f' | format(transferred / 1073741824) }} GiB{% else %}Unknown{% endif %}

**Average speed:** {% if transferred is not none and secs is not none and secs > 0 %}{{ '%.2f' | format(transferred / secs / 1048576) }} MiB/s{% else %}Unknown{% endif %}
{% if outcome == 'running' and rate is not none %}

**Current speed:** {{ '%.2f' | format(rate / 1048576) }} MiB/s
{% endif %}

**Runtime:** {% if secs is not none %}{{ '%02d:%02d:%02d' | format((secs // 3600) | int, ((secs % 3600) // 60) | int, (secs % 60) | int) }}{% else %}Unknown{% endif %}

{% for rows, label in [(successful, 'Successful remotes'), (failed, 'Failed remotes'), (skipped, 'Skipped remotes')] %}
{% if rows %}
**{{ label }}**

{% for row in rows[:dataset_limit] %}
- {{ (row.source | default(row.name | default('Unnamed remote'))) | e }}{% if row.destination | default('') %} → {{ row.destination | e }}{% endif %}{% if row.reason | default('') %}: {{ row.reason | e | replace('\n', ' ') }}{% endif %}
{% endfor %}
{% if rows | count > dataset_limit %}

_{{ rows | count - dataset_limit }} more; full list is in the sensor attributes._
{% endif %}

{% endif %}
{% endfor %}
{% if stderr %}
**Stderr — latest captured output**

<pre>{{ stderr | e }}</pre>
{% endif %}

{% endfor %}
{% if stats.records | count == 0 %}
**No matching live-preview devices detected.** Set the same manufacturer name in LiveConfig and at the top of this template, enable MQTT discovery, and start a job using the updated live module.
{% endif %}

_Running is determined by the live heartbeat. Expired live sensors use the retained result unless a different unfinished run is known. Unknown progress stays unknown. Steps are settled workflow observations, including failures and skips; destination progress is tracked independently. Generic jobs without detailed progress use their configured item unit. Average speed is a run average._
