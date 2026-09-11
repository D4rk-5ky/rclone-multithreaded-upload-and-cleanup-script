"""Optional MQTT publication of the completed run result."""

from datetime import datetime, timezone
import json
import time

from . import VERSION
from .output import print_error, print_step
from .results import remote_result_label
from .state import STATE


def _stage_errors(result) -> list[dict[str, str]]:
    """Flatten one remote's stage errors into stable JSON objects."""
    flattened: list[dict[str, str]] = []
    for stage_name, stage in (
        ("reservation", result.reservation),
        ("upload", result.upload),
        ("post_cleanup", result.post_cleanup),
        ("final_quota", result.final_quota),
    ):
        for error in stage.errors:
            flattened.append({"stage": stage_name, "error": error})
    return flattened


def build_result_payload(exit_code: int) -> dict:
    """Build the stable Home-Assistant-friendly JSON result payload."""
    with STATE.run_results_lock:
        remote_results = list(STATE.run_results.values())

        remotes = []
        combined_errors: list[str] = []
        failed_remotes: list[str] = []
        for result in remote_results:
            result_label = remote_result_label(result)
            errors = _stage_errors(result)
            if result_label == "FAILED":
                failed_remotes.append(result.name)
            for item in errors:
                combined_errors.append(
                    f"{result.name} - {item['stage']}: {item['error']}"
                )

            remotes.append(
                {
                    "name": result.name,
                    "remote_path": result.remote_path,
                    "status": result_label.lower(),
                    "stages": {
                        "reservation": result.reservation.status,
                        "upload": result.upload.status,
                        "post_cleanup": result.post_cleanup.status,
                        "final_quota": result.final_quota.status,
                    },
                    "errors": errors,
                }
            )

    success = exit_code == 0
    if not success and not combined_errors:
        combined_errors.append(f"Run failed with exit code {exit_code}")

    return {
        "schema_version": 1,
        "event": "rclone_multithreaded_upload_result",
        "script_name": STATE.script_name,
        "application": "rclone-multithreaded-upload",
        "application_version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "success" if success else "failure",
        "success": success,
        "exit_code": exit_code,
        "error": None if success else "\n".join(combined_errors),
        "failed_remotes": failed_remotes,
        "remotes": remotes,
    }


def _load_paho_mqtt():
    """Load the optional paho-mqtt dependency only when MQTT is enabled."""
    try:
        import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "MQTT publishing is enabled but the optional paho-mqtt package is not "
            "installed. Install it with: python3 -m pip install paho-mqtt"
        ) from error
    return mqtt


def _create_mqtt_client(mqtt, client_id: str | None):
    """Create a client compatible with paho-mqtt 2.x and older 1.x releases."""
    client_id_text = client_id or ""
    callback_api = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api is not None:
        return mqtt.Client(callback_api.VERSION2, client_id=client_id_text)
    return mqtt.Client(client_id=client_id_text)


def publish_result_payload(payload: dict) -> None:
    """Publish one JSON result payload and wait until the broker accepts it."""
    config = STATE.mqtt
    if not config.enabled:
        return
    if config.host is None:
        raise RuntimeError("MQTT is enabled but no broker host is configured")

    mqtt = _load_paho_mqtt()
    client = _create_mqtt_client(mqtt, config.client_id)

    if config.username is not None:
        client.username_pw_set(config.username, config.password)
    if config.tls:
        if config.ca_certs is None:
            client.tls_set()
        else:
            client.tls_set(ca_certs=config.ca_certs)
        if config.tls_insecure:
            client.tls_insecure_set(True)

    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    loop_started = False
    connected = False
    try:
        mqtt_success = getattr(mqtt, "MQTT_ERR_SUCCESS", 0)
        connect_rc = client.connect(config.host, config.port, config.keepalive)
        if connect_rc != mqtt_success:
            raise RuntimeError(f"MQTT connect returned error code {connect_rc}")
        connected = True

        loop_rc = client.loop_start()
        if loop_rc != mqtt_success:
            raise RuntimeError(f"MQTT network loop returned error code {loop_rc}")
        loop_started = True

        connection_deadline = time.monotonic() + config.publish_timeout
        while not client.is_connected():
            if time.monotonic() >= connection_deadline:
                raise TimeoutError(
                    f"MQTT broker connection was not established within "
                    f"{config.publish_timeout}s"
                )
            time.sleep(0.05)

        publish_info = client.publish(
            config.topic,
            payload_text,
            qos=config.qos,
            retain=config.retain,
        )
        if getattr(publish_info, "rc", mqtt_success) != mqtt_success:
            raise RuntimeError(
                f"MQTT publish returned error code {getattr(publish_info, 'rc', 'unknown')}"
            )

        publish_info.wait_for_publish(timeout=config.publish_timeout)
        if hasattr(publish_info, "is_published") and not publish_info.is_published():
            raise TimeoutError(
                f"MQTT result was not published within {config.publish_timeout}s"
            )
    finally:
        if connected:
            try:
                client.disconnect()
            except Exception:
                pass
        if loop_started:
            try:
                client.loop_stop()
            except Exception:
                pass


def publish_final_result(exit_code: int) -> bool | None:
    """Publish the completed run result; MQTT transport failure is non-fatal."""
    if not STATE.mqtt.enabled:
        return None

    payload = build_result_payload(exit_code)
    try:
        publish_result_payload(payload)
    except Exception as error:
        print_error(
            f"MQTT result publish failed for script '{STATE.script_name}': {error}"
        )
        return False

    print_step(
        f"MQTT result published for script '{STATE.script_name}' to {STATE.mqtt.topic}"
    )
    return True
