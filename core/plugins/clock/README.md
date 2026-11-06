# Clock

Time tools: the current time, countdown timers, stopwatches and one-shot
alarms. On by default. No settings.

For a reminder: `set_timer` ("in 20 minutes") or `set_alarm` ("at 7:30")
rings here. A reminder that should *say* something, repeat, or reach you on
another device is a scheduled task: `schedule_task` (see `docs/CONTINUITY.md`).

## Tools

| Tool | What it does |
|---|---|
| `get_time()` | Date and time, plus every active timer, stopwatch and alarm |
| `set_timer(name, time?, delete?)` | A countdown: `5m`, `30s`, `1h 10m` (a bare number is minutes). Pings when it runs out |
| `set_stopwatch(name, delete?)` | Starts a stopwatch; calling it again with the same name gives the elapsed time |
| `set_alarm(name, time?, delete?)` | A one-shot alarm at a time of day: `07:00`, `2:30pm` (tomorrow if that time has passed). Pings when it fires |

Reusing a name replaces that timer or alarm; `delete=true` cancels it.

## Good to know

- The ping is `ping.wav`, played on the speakers of the machine Sapphire runs
  on, not in the browser.
- Timers and stopwatches live in memory and are lost on a restart. Alarms are
  scheduled tasks, so they survive one: each shows in **Triggers > Scheduled**
  as `(alarm) <name>` and deletes itself after it fires.
