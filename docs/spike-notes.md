# Spike notes — pi RPC over E2B PTY

Run: 2026-07-10 14:49

## Results

- **setup/pi-install**: PASS 0.80.6
- **1-probe**: PASS
- **2-turn**: PASS stream updates seen: 6
- **3-steer**: PASS (soft: continue even if FAIL)
- **4-abort**: PASS daemon alive after abort: True
- **5a-restart-probe**: PASS
- **5b-continuity**: PASS
- **6-info**: INFO pi process survived pause/resume: False
- **6-info**: INFO old PTY reconnectable: True
- **6-pause-resume**: PASS continuity after pause/resume via restart-with--c

## Quirk log (non-JSON PTY lines & anomalies — future DaemonClient test cases)

- `base template node v20.9.0 too old for pi — upgraded to 22 via n`
- `non-JSON line: b"user@e2b:~$ stty -echo; export PS1=''"`
- `reader thread exception: CommandExitException(stderr='', stdout='', exit_code=-1, error='signal: killed')`
- `non-JSON line: b"user@e2b:~$ stty -echo; export PS1=''"`
- `reader thread exception: TimeoutException('<StreamReset stream_id:35, error_code:2, remote_reset:True>: The sandbox was killed or reached its end of life while the request was in flight.')`
- `non-JSON line: b"user@e2b:~$ stty -echo; export PS1=''"`
