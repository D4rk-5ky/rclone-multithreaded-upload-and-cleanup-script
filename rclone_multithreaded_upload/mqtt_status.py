"""Reusable Syncerate-compatible completion reports. Python 3.10+.

Payload building needs only the standard library. Publishing lazily imports
paho-mqtt in an isolated, timeout-bounded worker. See README.md for integration.
This is a reusable completion contract, not an upstream Syncerate module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import socket
import ssl
import subprocess
import sys
from typing import Any, Mapping, Sequence
import uuid

__version__ = "1.1.0"
WORKER_FLAG = "--mqtt-status-worker"
STDERR_LIMIT = 4000


def _text(value: Any, label: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{label} must be a {'nonempty ' if nonempty else ''}string")
    return value


def _json_text(value: Any) -> str:
    """Require real JSON types, finite numbers, string keys and valid UTF-8."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Report contains an unsupported JSON value")
    check(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    encoded.encode("utf-8")
    return encoded


def _dataset_list(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, dict)):
        raise ValueError("Dataset details must be a sequence of objects")
    result = []
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError("Each dataset detail must be an object")
        result.append(dict(item))
    return result


def build_report(
    *,
    name: str,
    job: str,
    exit_code: int,
    title: str | None = None,
    error_message: str | None = "",
    stderr_text: str | bytes | None = "",
    warning: bool = False,
    dry_run: bool = False,
    command: str = "",
    comment: str = "",
    version: str = "",
    run_id: str | None = None,
    skipped_datasets: Sequence[Mapping[str, Any]] | None = None,
    failed_datasets: Sequence[Mapping[str, Any]] | None = None,
    planned_datasets: Sequence[Mapping[str, Any]] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a detached JSON-compatible dictionary.

    exit_code is the authoritative *reported job result*: 0 means success,
    every other integer means failure (negative signal codes are allowed).
    Callers map their actual job semantics to that code; this function cannot
    verify backups or container updates. Warnings do not change the outcome.
    Extra top-level fields preserve project details but cannot replace core fields.
    """
    if type(exit_code) is not int:
        raise ValueError("exit_code must be an integer, not a boolean/string")
    if type(warning) is not bool or type(dry_run) is not bool:
        raise ValueError("warning and dry_run must be JSON booleans")
    if isinstance(stderr_text, bytes):
        stderr_text = stderr_text.decode("utf-8", errors="replace")
    success = exit_code == 0
    report = {
        "status": "success" if success else "failure",
        "success": success,
        "title": _text(title if title is not None else name, "title", nonempty=True),
        "name": _text(name, "name", nonempty=True),
        "job": _text(job, "job", nonempty=True),
        "dry_run": dry_run,
        "exit_code": exit_code,
        "error": _text(error_message if error_message is not None else "", "error_message"),
        "stderr": _text(stderr_text if stderr_text is not None else "", "stderr_text")[-STDERR_LIMIT:],
        "warning": warning,
        "schema_version": 1,
        "command": _text(command, "command"),
        "comment": _text(comment, "comment"),
        "version": _text(version, "version"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "run_id": _text(run_id if run_id is not None else str(uuid.uuid4()), "run_id", nonempty=True),
    }
    # Dataset diagnostics belong to adapters such as Syncerate, not the generic
    # completion contract. Omitted means absent; an explicit [] is preserved.
    for key, values in (("planned_datasets", planned_datasets),
                        ("skipped_datasets", skipped_datasets),
                        ("failed_datasets", failed_datasets)):
        if values is not None:
            report[key] = _dataset_list(values)
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise ValueError("extra must be an object")
        if set(extra).intersection(report):
            raise ValueError("extra cannot replace standard report fields")
        report.update(extra)
    # Round-trip also detaches nested objects supplied by the caller.
    return json.loads(encode_report(report))


def encode_report(report: Mapping[str, Any]) -> str:
    """Validate and serialize a template report as compact UTF-8 JSON.

    This validates this module's contract, not every historical Syncerate payload.
    Build legacy adapters through build_report rather than publishing unchecked JSON.
    """
    if not isinstance(report, Mapping):
        raise ValueError("report must be an object")
    report = dict(report)
    for key in ("title", "name", "job", "error", "stderr", "command", "comment",
                "version", "timestamp", "host", "run_id"):
        _text(report.get(key), key, nonempty=key in ("title", "name", "job", "run_id"))
    code = report.get("exit_code")
    if type(code) is not int:
        raise ValueError("exit_code must be an integer")
    for key in ("success", "warning", "dry_run"):
        if type(report.get(key)) is not bool:
            raise ValueError(f"{key} must be a JSON boolean")
    success = code == 0
    if report["success"] != success or report.get("status") != ("success" if success else "failure"):
        raise ValueError("status, success and exit_code must agree")
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    for key in ("planned_datasets", "skipped_datasets", "failed_datasets"):
        if key in report and (not isinstance(report[key], list) or any(not isinstance(x, dict) for x in report[key])):
            raise ValueError(f"{key} must be an array of objects")
    return _json_text(report)


def build_mqtt_status_payload(**kwargs: Any) -> str:
    """Convenience JSON-string builder; uses build_report's arguments.

    The name follows Syncerate, but the signature is intentionally independent
    of Syncerate AppConfig/ReplicationSummary and is not a drop-in function replacement.
    """
    return encode_report(build_report(**kwargs))


@dataclass(frozen=True)
class MQTTConfig:
    enabled: bool = False
    host: str = ""
    topic: str = "homeassistant/jobs/status"
    port: int = 1883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    client_id: str = ""
    qos: int = 0
    timeout: float = 15.0
    on_success: bool = True
    send_on_dry_run: bool = True
    tls: bool = False
    ca_certs: str | None = None
    certfile: str | None = None
    keyfile: str | None = None

    def __post_init__(self) -> None:
        for key in ("enabled", "on_success", "send_on_dry_run", "tls"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        host = _text(self.host, "host", nonempty=self.enabled)
        if "\x00" in host or "://" in host or host != host.strip():
            raise ValueError("host must be a hostname/IP without a scheme or surrounding whitespace")
        topic = _text(self.topic, "topic", nonempty=True)
        if any(x in topic for x in ("+", "#", "\x00")) or len(topic.encode("utf-8")) > 65535:
            raise ValueError("topic must be a valid MQTT publish topic without wildcards")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer from 1 to 65535")
        if type(self.qos) is not int or self.qos not in (0, 1, 2):
            raise ValueError("qos must be 0, 1 or 2")
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a finite positive number of seconds")
        _text(self.client_id, "client_id")
        for key in ("username", "password", "ca_certs", "certfile", "keyfile"):
            if getattr(self, key) is not None:
                _text(getattr(self, key), key)
        if self.password and not self.username:
            raise ValueError("password requires username")
        if bool(self.certfile) != bool(self.keyfile):
            raise ValueError("certfile and keyfile must be provided together")
        if any((self.ca_certs, self.certfile, self.keyfile)) and not self.tls:
            raise ValueError("certificate options require tls=true")


@dataclass(frozen=True)
class PublishResult:
    status: str  # sent, skipped, failed (delivery status, never the job status)
    reason: str

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def publish_report(config: MQTTConfig, report: Mapping[str, Any]) -> PublishResult:
    """Best-effort delivery; never replaces the caller's job code.

    Disabled MQTT does no serialization, subprocess work or Paho import.
    Failures always pass the on_success gate; dry-run suppression is separate.
    No retries: callers should not blindly replay a completion event.
    """
    if not config.enabled:
        return PublishResult("skipped", "MQTT disabled")
    try:
        # Snapshot the caller's report once for consistent gating and transmission.
        snapshot = json.loads(encode_report(report))
        if snapshot["success"] and not config.on_success:
            return PublishResult("skipped", "Success reporting disabled")
        if snapshot["dry_run"] and not config.send_on_dry_run:
            return PublishResult("skipped", "Dry-run reporting disabled")
        command = ([sys.executable, WORKER_FLAG] if getattr(sys, "frozen", False) else
                   [sys.executable, "-B", str(Path(__file__).resolve()), WORKER_FLAG])
        request = _json_text({"config": asdict(config), "report": snapshot})
        result = subprocess.run(command, input=request, text=True, encoding="utf-8",
                                capture_output=True, timeout=config.timeout, check=False)
        if result.returncode != 0:
            return PublishResult("failed", "Publisher failed; check dependency, broker, authentication and TLS")
        return PublishResult("sent", "MQTT publish completed; Home Assistant receipt is not confirmed")
    except subprocess.TimeoutExpired:
        return PublishResult("failed", "Publisher timed out; delivery is unconfirmed")
    except Exception:
        # Never echo exceptions or worker output: either may contain credentials.
        return PublishResult("failed", "Could not validate report or run MQTT publisher")


def _publish_once(config: MQTTConfig, report: Mapping[str, Any]) -> None:
    from paho.mqtt.publish import single

    context = None
    if config.tls:
        context = ssl.create_default_context(cafile=config.ca_certs or None)
        if config.certfile:
            context.load_cert_chain(config.certfile, config.keyfile)
    auth = {"username": config.username, "password": config.password} if config.username else None
    single(config.topic, payload=encode_report(report), hostname=config.host,
           port=config.port, client_id=config.client_id, qos=config.qos,
           retain=False, auth=auth, tls=context)


def run_worker_if_requested() -> bool:
    """Call before your main argument parser, especially in PyInstaller builds.

    Normal invocation returns False. A private worker invocation exits here.
    The frozen executable must bundle paho.mqtt and call this hook at startup.
    """
    if sys.argv[1:] != [WORKER_FLAG]:
        return False
    try:
        request = json.load(sys.stdin)
        config = MQTTConfig(**request["config"])
        if not config.enabled:
            raise ValueError("worker requires enabled MQTT")
        _publish_once(config, request["report"])
    except Exception:
        raise SystemExit(1) from None
    raise SystemExit(0)


if __name__ == "__main__":
    run_worker_if_requested()
    print("Import this module from your application; see README.md for examples.", file=sys.stderr)
    raise SystemExit(2)
