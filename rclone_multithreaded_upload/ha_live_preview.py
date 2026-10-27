"""Independent Home Assistant live job preview. Python 3.10+, optional Paho.

No dependency on mqtt_status.py. Application updates only local memory; a
daemon batches MQTT work into killable subprocesses. See LIVE_PREVIEW.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from typing import Any
import uuid

__version__ = "1.1.0"
WORKER_FLAG = "--ha-live-preview-worker"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    result.encode("utf-8")
    return result


def make_device_id(job: str, config_key: str, host: str | None = None) -> str:
    """Stable per-host/per-config identity. config_key should not contain secrets."""
    for value in (job, config_key):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("job and config_key must be nonempty strings")
    label = re.sub(r"[^a-z0-9_]+", "_", job.lower()).strip("_")[:32] or "job"
    digest = hashlib.sha256(_json([host if host is not None else socket.gethostname(), job, config_key]).encode()).hexdigest()[:12]
    return f"{label}_{digest}"


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = False
    host: str = ""
    port: int = 1883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    client_id: str = ""
    qos: int = 1
    timeout: float = 5.0
    tls: bool = False
    ca_certs: str | None = None
    certfile: str | None = None
    keyfile: str | None = None
    device_id: str = "example_job"
    topic_prefix: str = "job-live"
    discovery: bool = True
    send_on_dry_run: bool = False
    discovery_prefix: str = "homeassistant"
    heartbeat_seconds: float = 10.0
    min_publish_interval: float = 1.0
    expire_after: int = 45
    discovery_interval: float = 300.0
    final_wait: float = 1.0

    def __post_init__(self) -> None:
        for key in ("enabled", "tls", "discovery", "send_on_dry_run"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        if not isinstance(self.host, str) or (self.enabled and not self.host.strip()) or "://" in self.host or "\x00" in self.host or self.host != self.host.strip():
            raise ValueError("host must be a hostname/IP without a scheme or surrounding whitespace")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer from 1 to 65535")
        if type(self.qos) is not int or self.qos not in (0, 1, 2):
            raise ValueError("qos must be 0, 1 or 2")
        if not isinstance(self.device_id, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", self.device_id):
            raise ValueError("device_id must contain 1-80 lowercase letters, digits or underscores")
        for key in ("topic_prefix", "discovery_prefix"):
            value = getattr(self, key)
            if not isinstance(value, str) or not value.strip("/").strip() or any(c in value for c in "+#\x00") or len(value.encode()) > 65000:
                raise ValueError(f"{key} must be a nonempty MQTT topic prefix without wildcards")
        for key in ("timeout", "heartbeat_seconds", "min_publish_interval", "discovery_interval", "final_wait"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be finite and greater than zero")
        if self.min_publish_interval > self.heartbeat_seconds:
            raise ValueError("min_publish_interval cannot exceed heartbeat_seconds")
        if type(self.expire_after) is not int or self.expire_after <= 2 * self.heartbeat_seconds + self.timeout:
            raise ValueError("expire_after must exceed two heartbeats plus the publish timeout")
        for key in ("username", "password", "ca_certs", "certfile", "keyfile"):
            if getattr(self, key) is not None and not isinstance(getattr(self, key), str):
                raise ValueError(f"{key} must be a string or null")
        if not isinstance(self.client_id, str):
            raise ValueError("client_id must be a string")
        if self.password and not self.username:
            raise ValueError("password requires username")
        if bool(self.certfile) != bool(self.keyfile):
            raise ValueError("certfile and keyfile must be provided together")
        if any((self.ca_certs, self.certfile, self.keyfile)) and not self.tls:
            raise ValueError("certificate options require tls=true")

    @property
    def state_topic(self) -> str:
        return f"{self.topic_prefix.rstrip('/')}/{self.device_id}/state"

    @property
    def result_topic(self) -> str:
        return f"{self.topic_prefix.rstrip('/')}/{self.device_id}/last_result"


def discovery_messages(config: LiveConfig, name: str, version: str = "") -> list[dict[str, Any]]:
    """Retained single-component discovery; state is intentionally non-retained."""
    if not config.discovery:
        return []
    fields = [
        ("status", "Status", None), ("progress_percent", "Progress", "%"),
        ("completed", "Completed items", None), ("total", "Total items", None),
        ("current_item", "Current item", None), ("phase", "Phase", None),
        ("runtime_seconds", "Runtime", "s"), ("transferred_bytes", "Transferred", "B"),
        ("bytes_per_second", "Transfer speed", "B/s"),
        ("last_heartbeat", "Last heartbeat", None), ("last_result", "Last result", None),
        ("successful_dataset_count", "Successful datasets", None),
        ("failed_dataset_count", "Failed datasets", None),
        ("skipped_dataset_count", "Skipped datasets", None),
    ]
    messages = []
    for key, label, unit in fields:
        result = key == "last_result"
        field_name = "status" if result else key
        identifier = f"job_live_{config.device_id}_{key}"
        payload = {
            "name": label, "unique_id": identifier,
            "default_entity_id": f"sensor.{identifier}",
            "device": {"identifiers": [f"job_live_{config.device_id}"], "name": name,
                       "manufacturer": "MQTT job template", "model": "Live job preview",
                       "sw_version": version or __version__},
            "origin": {"name": "ha_live_preview", "sw_version": __version__},
            "state_topic": config.result_topic if result else config.state_topic,
            "value_template": "{{ value_json." + field_name + " }}",
            "qos": config.qos,
        }
        if not result:
            payload["expire_after"] = config.expire_after
        if unit:
            payload["unit_of_measurement"] = unit
        if key in ("completed", "total", "progress_percent", "runtime_seconds", "transferred_bytes", "bytes_per_second",
                   "successful_dataset_count", "failed_dataset_count", "skipped_dataset_count"):
            payload["state_class"] = "measurement"
        if key == "last_heartbeat":
            payload["device_class"] = "timestamp"
        if key in ("current_item", "phase"):
            payload["value_template"] = "{{ value_json." + key + "[:255] }}"
        if result:
            payload["json_attributes_topic"] = config.result_topic
        elif key == "status":
            payload["json_attributes_topic"] = config.state_topic
            payload["json_attributes_template"] = (
                '{{ {"stderr": value_json.stderr, "error": value_json.error, '
                '"run_id": value_json.run_id, "dry_run": value_json.dry_run, '
                '"name": value_json.name, "job": value_json.job, '
                '"successful_datasets": value_json.successful_datasets, '
                '"failed_datasets": value_json.failed_datasets, '
                '"skipped_datasets": value_json.skipped_datasets} | tojson }}'
            )
        messages.append({"topic": f"{config.discovery_prefix.rstrip('/')}/sensor/{identifier}/config",
                         "payload": payload, "retain": True, "qos": config.qos})
    return messages


class LivePreview:
    """One run per instance. Caller owns the real job lock and progress counters.

    start/update/append_stderr only update memory or launch a daemon; never do
    network I/O on the job thread. finish has a bounded best-effort final drain.
    """

    def __init__(self, config: LiveConfig, *, name: str, job: str, version: str = "",
                 run_id: str | None = None, dry_run: bool = False):
        for value in (name, job):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("name and job must be nonempty strings")
        if not isinstance(version, str) or type(dry_run) is not bool:
            raise ValueError("version must be a string and dry_run a boolean")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError("run_id must be a nonempty string")
        self.config, self.name, self.job, self.version = config, name, job, version
        self.run_id, self.dry_run = run_id or str(uuid.uuid4()), dry_run
        self._lock, self._process_lock = Lock(), Lock()
        self._wake, self._stop = Event(), Event()
        self._thread: Thread | None = None
        self._process: subprocess.Popen | None = None
        self._started = self._finished = False
        self._dirty = True
        self._start_time = time.monotonic()
        self._end_time: float | None = None
        self._started_at = _now()
        self._result: dict[str, Any] | None = None
        self._delivery = "not_started"
        self._state = {"status": "idle", "success": None, "exit_code": None,
                       "phase": "waiting", "current_item": "", "completed": None, "total": None,
                       "progress_percent": None, "transferred_bytes": None, "bytes_per_second": None,
                       "successful_datasets": [], "failed_datasets": [], "skipped_datasets": [],
                       "successful_dataset_count": 0, "failed_dataset_count": 0, "skipped_dataset_count": 0,
                       "stderr": "", "error": "", "warning": False, "message": "", "finished_at": None}

    def _snapshot_locked(self) -> dict[str, Any]:
        return json.loads(_json({"schema_version": 1, "name": self.name, "title": self.name, "job": self.job,
                "device_id": self.config.device_id, "run_id": self.run_id,
                "host": socket.gethostname(), "version": self.version, "dry_run": self.dry_run,
                "started_at": self._started_at, "last_heartbeat": _now(),
                "runtime_seconds": round(max(0, (self._end_time if self._end_time is not None else time.monotonic()) - self._start_time), 2),
                **self._state}))

    def snapshot(self) -> dict[str, Any]:
        """Local snapshot, not proof of delivery. Unknown measurements stay null."""
        with self._lock:
            return self._snapshot_locked()

    @property
    def last_delivery(self) -> str:
        with self._lock:
            return self._delivery

    def start(self) -> bool:
        with self._lock:
            if self._started or self._finished or self._stop.is_set():
                return False
            self._started = True
            self._start_time, self._started_at = time.monotonic(), _now()
            self._state.update(status="running", phase="starting")
            if not self.config.enabled or (self.dry_run and not self.config.send_on_dry_run):
                return False
            self._thread = Thread(target=self._worker, daemon=True, name=f"ha-live-{self.config.device_id}")
        try:
            self._thread.start()
            return True
        except Exception:
            with self._lock:
                self._delivery = "failed"
            return False

    def update(self, **values: Any) -> bool:
        """Local coalesced update. Counters refer to items with one caller-defined unit."""
        allowed = {"phase", "current_item", "completed", "total", "transferred_bytes",
                   "bytes_per_second", "stderr", "message", "successful_datasets",
                   "failed_datasets", "skipped_datasets"}
        if set(values) - allowed:
            raise ValueError("Unsupported progress field")
        for key, value in values.items():
            if key.endswith("_datasets"):
                if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                    raise ValueError(f"{key} must be an array of objects")
            elif key in ("phase", "current_item", "stderr", "message"):
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
            elif value is not None:
                if key == "bytes_per_second":
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("bytes_per_second must be a finite nonnegative number or null")
                elif type(value) is not int or value < 0:
                    raise ValueError(f"{key} must be a nonnegative integer or null")
        if "stderr" in values:
            values["stderr"] = values["stderr"][-4000:]
        values = json.loads(_json(values))
        with self._lock:
            if self._finished:
                return False
            merged = {**self._state, **values}
            done, total = merged["completed"], merged["total"]
            if done is not None and total is not None and done > total:
                raise ValueError("completed cannot exceed total")
            merged["progress_percent"] = round(100 * done / total, 2) if done is not None and total else None
            for outcome in ("successful", "failed", "skipped"):
                merged[outcome + "_dataset_count"] = len(merged[outcome + "_datasets"])
            self._state, self._dirty = merged, True
        self._wake.set()
        return True

    def record_dataset(self, outcome: str, *, source: str, destination: str = "", reason: str = "") -> bool:
        """Record a dataset's latest outcome, keyed by (source, destination).

        A retry outcome replaces the previous classification. This is current
        per-dataset state, not attempt history. Generic item counters are separate.
        """
        groups = {"success": "successful_datasets", "failure": "failed_datasets", "skipped": "skipped_datasets"}
        if outcome not in groups:
            raise ValueError("dataset outcome must be success, failure or skipped")
        if not isinstance(source, str) or not source.strip() or any(not isinstance(x, str) for x in (destination, reason)):
            raise ValueError("source must be nonempty text; destination and reason must be strings")
        row = {"source": source, "destination": destination}
        if reason:
            row["reason"] = reason
        _json(row)
        with self._lock:
            if self._finished:
                return False
            for group in groups.values():
                self._state[group] = [item for item in self._state[group]
                                      if (item.get("source"), item.get("destination", "")) != (source, destination)]
            self._state[groups[outcome]].append(row)
            for label in ("successful", "failed", "skipped"):
                self._state[label + "_dataset_count"] = len(self._state[label + "_datasets"])
            self._dirty = True
        self._wake.set()
        return True

    def append_stderr(self, output: str | bytes) -> bool:
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if not isinstance(output, str):
            raise ValueError("stderr output must be text or bytes")
        with self._lock:
            if self._finished:
                return False
            self._state["stderr"] = (self._state["stderr"] + output)[-4000:]
            self._dirty = True
        self._wake.set()
        return True

    def finish(self, exit_code: int, *, error: str = "", stderr: str | None = None,
               warning: bool = False, interrupted: bool = False, incomplete: bool = False) -> bool:
        if type(exit_code) is not int or not isinstance(error, str):
            raise ValueError("exit_code must be an integer and error a string")
        if any(type(x) is not bool for x in (warning, interrupted, incomplete)):
            raise ValueError("terminal flags must be booleans")
        if interrupted and (exit_code == 0 or incomplete):
            raise ValueError("interrupted requires a nonzero code and cannot also be incomplete")
        if stderr is not None and not isinstance(stderr, str):
            raise ValueError("stderr must be a string or null")
        with self._lock:
            if self._finished:
                return False
            done, total = self._state["completed"], self._state["total"]
            remaining = (done is not None and total is not None and done < total) or bool(self._state["failed_datasets"])
            status = ("interrupted" if interrupted else "failure" if exit_code != 0 else
                      "incomplete" if incomplete or remaining else "success")
            self._end_time = time.monotonic()
            self._state.update(status=status, success=status == "success", exit_code=exit_code,
                               error=error, warning=warning, phase="finished", finished_at=_now())
            if stderr is not None:
                self._state["stderr"] = stderr[-4000:]
            self._finished = self._dirty = True
            self._result = self._snapshot_locked()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.config.final_wait)
            if self._thread.is_alive():
                self._cancel()
        return True

    def _cancel(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._process_lock:
            if self._process is not None:
                try:
                    self._process.kill()
                except OSError:
                    pass

    def close(self) -> None:
        """Stop without inventing an outcome; a previously running sensor expires."""
        self._cancel()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.config.final_wait)

    def _send(self, messages: list[dict[str, Any]]) -> bool:
        request = _json({"config": asdict(self.config), "messages": messages})
        command = ([sys.executable, WORKER_FLAG] if getattr(sys, "frozen", False) else
                   [sys.executable, "-B", str(Path(__file__).resolve()), WORKER_FLAG])
        process = None
        try:
            with self._process_lock:
                if self._stop.is_set():
                    return False
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
                self._process = process
            process.communicate(input=request, timeout=self.config.timeout)
            return process.returncode == 0
        except Exception:
            if process is not None:
                try:
                    process.kill()
                    process.communicate(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return False
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None

    def _worker(self) -> None:
        next_allowed = heartbeat_due = discovery_due = 0.0
        try:
            while not self._stop.is_set():
                with self._lock:
                    dirty, finished = self._dirty, self._finished
                due = 0 if finished else next_allowed if dirty else heartbeat_due
                delay = max(0, due - time.monotonic())
                if delay:
                    self._wake.wait(timeout=delay)
                    self._wake.clear()
                    continue
                with self._lock:
                    self._dirty = False
                    finished, result = self._finished, self._result
                    state = self._snapshot_locked()
                messages = []
                now = time.monotonic()
                include_discovery = self.config.discovery and now >= discovery_due
                if include_discovery:
                    messages.extend(discovery_messages(self.config, self.name, self.version))
                messages.append({"topic": self.config.state_topic, "payload": state,
                                 "qos": self.config.qos, "retain": False})
                if result is not None and not self.dry_run:
                    messages.append({"topic": self.config.result_topic, "payload": result,
                                     "qos": self.config.qos, "retain": True})
                ok = self._send(messages)
                with self._lock:
                    self._delivery = "sent" if ok else "failed"
                if ok and include_discovery:
                    discovery_due = time.monotonic() + self.config.discovery_interval
                if finished:
                    return
                next_allowed = time.monotonic() + self.config.min_publish_interval
                heartbeat_due = time.monotonic() + self.config.heartbeat_seconds
        except Exception:
            with self._lock:
                self._delivery = "failed"


def run_worker_if_requested() -> bool:
    """Call before the app parser, together with mqtt_status's separate hook."""
    if sys.argv[1:] != [WORKER_FLAG]:
        return False
    try:
        request = json.load(sys.stdin)
        config = LiveConfig(**request["config"])
        if not config.enabled:
            raise ValueError("worker requires enabled monitoring")
        from paho.mqtt.publish import multiple
        context = None
        if config.tls:
            context = ssl.create_default_context(cafile=config.ca_certs or None)
            if config.certfile:
                context.load_cert_chain(config.certfile, config.keyfile)
        messages = [{**item, "payload": _json(item["payload"])} for item in request["messages"]]
        auth = {"username": config.username, "password": config.password} if config.username else None
        multiple(messages, hostname=config.host, port=config.port, auth=auth, tls=context,
                 client_id=config.client_id or f"ha-live-{config.device_id}")
    except Exception:
        raise SystemExit(1) from None
    raise SystemExit(0)


if __name__ == "__main__":
    run_worker_if_requested()
    print("Import this module; see LIVE_PREVIEW.md.", file=sys.stderr)
    raise SystemExit(2)
