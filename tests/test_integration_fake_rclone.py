"""End-to-end integration test using a fake rclone executable.

This executes the real compatibility entry point and all package phases without
contacting a real provider or deleting cloud data.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


FAKE_RCLONE = r'''#!/usr/bin/python3 -S
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

state_path = Path(os.environ["FAKE_RCLONE_STATE"])
log_path = Path(os.environ["FAKE_RCLONE_LOG"])
args = sys.argv[1:]


def log(event, remote=""):
    record = {"time": time.monotonic(), "event": event, "remote": remote, "args": args}
    with log_path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def with_state(mutator):
    with state_path.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        state = json.load(fh)
        result = mutator(state)
        fh.seek(0)
        json.dump(state, fh)
        fh.truncate()
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return result


def read_state():
    with state_path.open("r", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        state = json.load(fh)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return state


if args[:2] == ["config", "dump"]:
    print(json.dumps({"fast": {"type": "drive"}, "slow": {"type": "drive"}}))
    sys.exit(0)

if args and args[0] == "lsjson":
    remote = args[-1]
    log("lsjson-start", remote)
    if remote.startswith("slow:"):
        time.sleep(0.80)
    state = read_state()
    entries = state["remotes"].get(remote, [])
    print(json.dumps([
        {"Path": item["path"], "Size": item["size"], "ModTime": item["modified"], "IsDir": False}
        for item in entries
    ]))
    log("lsjson-end", remote)
    sys.exit(0)

if args and args[0] == "size":
    local_path = Path(args[1])
    total = 0
    count = 0
    for path in local_path.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
            count += 1
    log("size", str(local_path))
    print(json.dumps({"bytes": total, "count": count}))
    sys.exit(0)

if args and args[0] == "delete":
    if "--files-from" in args:
        index = args.index("--files-from")
        list_path = Path(args[index + 1])
        remote = args[index + 2]
        selected = {line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()}

        def mutate(state):
            state["remotes"][remote] = [
                item for item in state["remotes"].get(remote, [])
                if item["path"] not in selected
            ]
        with_state(mutate)
        log("delete-files-from", remote)
        sys.exit(0)

    remote = args[1]
    log("delete-age", remote)
    sys.exit(0)

if args and args[0] == "cleanup":
    remote = args[1]
    log("cleanup", remote)
    sys.exit(0)

if args and args[0] in {"copy", "sync", "move"}:
    command, local_text, remote = args[:3]
    local_path = Path(local_text)
    now = datetime.now(timezone.utc).isoformat()
    uploaded = []
    for path in local_path.rglob("*"):
        if path.is_file():
            uploaded.append({
                "path": path.relative_to(local_path).as_posix(),
                "size": path.stat().st_size,
                "modified": now,
            })

    def mutate(state):
        existing = {item["path"]: item for item in state["remotes"].get(remote, [])}
        if command == "sync":
            existing = {}
        for item in uploaded:
            existing[item["path"]] = item
        state["remotes"][remote] = list(existing.values())

    with_state(mutate)
    log(command, remote)
    if command == "move":
        for path in local_path.rglob("*"):
            if path.is_file():
                path.unlink()
    print("Transferred: fake integration upload")
    sys.exit(0)

print(f"unsupported fake rclone args: {args}", file=sys.stderr)
sys.exit(2)
'''


class FakeRcloneIntegrationTests(unittest.TestCase):
    def test_complete_application_flow_and_independent_pipeline(self):
        project_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as temp_text:
            temp = Path(temp_text)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_rclone = fake_bin / "rclone"
            fake_rclone.write_text(FAKE_RCLONE, encoding="utf-8")
            fake_rclone.chmod(0o755)

            fast_local = temp / "fast-local"
            slow_local = temp / "slow-local"
            fast_local.mkdir()
            slow_local.mkdir()
            (fast_local / "new.bin").write_bytes(b"F" * (300 * 1024))
            (slow_local / "new.bin").write_bytes(b"S" * (300 * 1024))

            state_path = temp / "state.json"
            old_a = "2026-01-01T00:00:00Z"
            old_b = "2026-02-01T00:00:00Z"
            initial_remote = [
                {"path": "old-a.bin", "size": 900 * 1024, "modified": old_a},
                {"path": "old-b.bin", "size": 900 * 1024, "modified": old_b},
            ]
            state_path.write_text(
                json.dumps(
                    {
                        "remotes": {
                            "fast:root": list(initial_remote),
                            "slow:root": list(initial_remote),
                        }
                    }
                ),
                encoding="utf-8",
            )
            log_path = temp / "rclone.log"
            log_path.write_text("", encoding="utf-8")

            config_path = temp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "delete_min_age": "31d",
                        "lock_file": str(temp / "app.lock"),
                        "delete_list_dir": str(temp / "delete-lists"),
                        "sleep_after_step": 0,
                        "thread_limits": {
                            "upload_threads": 2,
                            "cleanup_threads": 2,
                            "remote_quota_cleanup_threads": 2,
                            "trash_cleanup_threads": 2,
                        },
                        "upload_directories": [
                            {
                                "name": "Fast",
                                "local_path": str(fast_local),
                                "remote_path": "fast:root",
                                "upload_command": "copy",
                                "delete_old_files": False,
                                "delete_excess_files": True,
                                "max_total_size": "2M",
                                "delete_to_trash": False,
                                "empty_trash": False,
                                "buffer_size": "16M",
                                "cleanup_rules": [{"path": "/", "max_size": "2M"}],
                                "copy_options": [],
                            },
                            {
                                "name": "Slow",
                                "local_path": str(slow_local),
                                "remote_path": "slow:root",
                                "upload_command": "copy",
                                "delete_old_files": False,
                                "delete_excess_files": True,
                                "max_total_size": "2M",
                                "delete_to_trash": False,
                                "empty_trash": False,
                                "buffer_size": "16M",
                                "cleanup_rules": [{"path": "/", "max_size": "2M"}],
                                "copy_options": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_RCLONE_STATE"] = str(state_path)
            env["FAKE_RCLONE_LOG"] = str(log_path)
            env["PYTHONPATH"] = str(project_root)

            try:
                result = subprocess.run(
                    [
                        str(project_root / "rclone-multithreaded-upload.py"),
                        "--config",
                        str(config_path),
                    ],
                    cwd=project_root,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=20,
                )
            except subprocess.TimeoutExpired as error:
                output = error.stdout or ""
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
                log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else "<missing log>"
                state_text = state_path.read_text(encoding="utf-8") if state_path.exists() else "<missing state>"
                self.fail(
                    "application timed out; output tail follows:\n"
                    + output[-12000:]
                    + "\nFAKE LOG:\n"
                    + log_text[-8000:]
                    + "\nSTATE:\n"
                    + state_text
                )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("OVERALL RESULT: SUCCESS", result.stdout)

            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            for remote in ("fast:root", "slow:root"):
                files = final_state["remotes"][remote]
                self.assertEqual([item["path"] for item in files], ["new.bin"])
                self.assertLessEqual(sum(item["size"] for item in files), 2 * 1024**2)

            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            delete_remotes = {
                item["remote"] for item in records if item["event"] == "delete-files-from"
            }
            copy_remotes = {item["remote"] for item in records if item["event"] == "copy"}
            self.assertEqual(delete_remotes, {"fast:root", "slow:root"})
            self.assertEqual(copy_remotes, {"fast:root", "slow:root"})

            fast_copy_time = min(
                item["time"]
                for item in records
                if item["event"] == "copy" and item["remote"] == "fast:root"
            )
            slow_reservation_listing_end = next(
                item["time"]
                for item in records
                if item["event"] == "lsjson-end" and item["remote"] == "slow:root"
                and item["time"] > min(
                    x["time"] for x in records if x["event"] == "size" and x["remote"] == str(slow_local)
                )
            )
            self.assertLess(
                fast_copy_time,
                slow_reservation_listing_end,
                "fast remote should upload before slow remote reservation listing finishes",
            )


if __name__ == "__main__":
    unittest.main()
