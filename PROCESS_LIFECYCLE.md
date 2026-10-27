# Process ownership and stopping

Run the normal CLI entry point:

```bash
python3 rclone-multithreaded-upload.py --config config.json
```

No lifecycle config keys are required. The config examples retain every available application setting. Help, version and config validation still run directly without locks, rclone work or MQTT publishing.

## Ownership

The entry point starts an outer supervisor and a job in a fresh POSIX session/process group. Existing rclone commands and MQTT publisher subprocesses inherit that group, as do their ordinary children. Commands, backend flags, cleanup eligibility and normal stage barriers keep their previous behavior. There is no process-name matching or global kill operation.

The supervisor keeps a pipe open. A daemon watchdog in the job reads the other end; EOF means the supervisor has died. This detects SIGKILL without pretending to catch SIGKILL in Python. The watchdog then SIGKILLs its isolated group, including itself. If the job dies first, the supervisor kills the group before exiting. Normal exits also remove lingering descendants. Linux child-subreaper support collects adopted descendants while the supervisor remains alive.

A second pipe reports signals delivered directly to the inner job PID back to the supervisor. Its file descriptors are non-inheritable in launched workers, so workers cannot keep either ownership pipe falsely alive.

## Stop commands

```bash
kill -TERM SUPERVISOR_PID   # same signal as plain kill PID
kill -INT SUPERVISOR_PID    # same signal delivered by Ctrl-C
kill -HUP SUPERVISOR_PID
kill -QUIT SUPERVISOR_PID
kill -KILL SUPERVISOR_PID   # forced stop; watchdog handles remaining group
```

The same signals may target the job PID shown in htop. These placeholders mean the PIDs belonging to this invocation, not rclone workers or unrelated processes. Normal shell use sends Ctrl-C to the foreground supervisor, which forwards it to the job group.

For catchable stop signals, cancellation prevents new command launches and queued pool jobs, and wakes configured worker waits. Pools cancel pending futures and join running threads. On Linux, after a 0.3-second grace period the supervisor finds remaining members of its own group/session through /proc and uses pidfds to force-stop child processes without PID-reuse races, leaving the job alive to finalize. This includes stubborn helpers holding inherited output pipes open.

The whole job has a five-second shutdown window, including best-effort final live/completion reporting. A stuck job is then force-killed. A second catchable stop signal sent to the supervisor forces immediate group cleanup. Notification workers remain independently configured and do not replace the interrupted job exit code. A stopped child or forced shutdown can prevent final delivery.

Shell exit codes are 130 for SIGINT, 143 for SIGTERM, 129 for SIGHUP and 131 for SIGQUIT. Killing the job with SIGKILL returns 137 through the supervisor. Killing the supervisor itself is reported by the shell as signal 9 (typically 137); its watchdog still stops the job group.

## Limits

The tested target is Linux using the documented CLI, with /proc, process groups, pidfds and child-subreaper support. Non-POSIX normal jobs are explicitly rejected. Other POSIX systems retain group/watchdog/final-deadline logic but Linux-specific reaping and early child escalation are not exercised there. Frozen builds were not built or tested.

Calling main() directly as a Python library bypasses the outer supervisor. Deliberately detached children that create a different session/process group are outside the inherited group; this application never launches its rclone/MQTT workers that way. Independently SIGKILLing both supervisor and watchdog job eliminates both monitors. No userspace program can promise immediate removal of a process stuck in uninterruptible kernel I/O, or cleanup after a machine crash.

SIGKILL, watchdog force-kill and final-deadline force-kill cannot execute final notifications or lock removal. A stale lock is intentionally not removed by guessing; check the old job/group before manually removing it. Ordinary graceful termination retains lock ownership through reporting and then releases it. Transfers/deletions completed before stopping are not rolled back.

## Offline verification

The lifecycle tests use the real CLI, fake rclone/Paho modules, multiple active destinations and descendants that ignore SIGTERM/SIGINT. They cover SIGINT, SIGTERM, SIGKILL of either app process, directly signalling the inner job, normal-exit leftovers, an unresponsive job, repeated stop signals, queued-work cancellation, interruptible waits, and an unrelated sentinel that must remain alive. Existing tests verify normal command behavior and safety rules. No real cloud upload/deletion or MQTT broker is used.
