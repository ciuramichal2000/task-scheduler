# taskscheduler

A cron-style scheduler in pure Python. It parses standard five-field cron expressions, computes the next run times for them, and includes a `Scheduler` class that registers named jobs, runs the ones that are due, and records when each last ran and what went wrong. The clock and the sleep function are injectable, so the whole thing can be tested without waiting.

## What is interesting in it

- **Next-run search without minute-by-minute stepping.** It skips non-matching months and days, then takes the hour from the sorted allowed values and the minute with a binary search. Month lengths and leap years (including the 2100 non-leap century) are handled.
- **The day-of-month / day-of-week rule of Vixie cron.** When both fields are restricted, a day matches if *either* matches (`0 0 13 * FRI` = the 13th or any Friday). If one of them starts with `*`, both must match (`0 0 */2 * MON` = odd-numbered Mondays).
- **Strict validation with specific messages.** Errors name the field and the offending text (`minute: 61 is out of range 0-59`). Schedules that could never fire, such as `0 0 31 2 *`, are rejected at parse time instead of silently doing nothing.
- **Scheduler state is one `next_due` per job.** A job is advanced before it runs, so it cannot run twice in the same minute however often `run_due()` is called, a clock that jumps backwards cannot re-run it, and several missed minutes become one late run instead of a burst. A failing job is recorded (type, message, time, bounded history) and does not stop the other jobs.
- **Deterministic by construction.** The scheduler never reads the real time or sleeps by itself: it takes a `clock` callable, and `run_forever` takes `sleep` and `should_stop` callables.

## Usage

```python
from datetime import datetime, timedelta
from taskscheduler import CronExpression, Scheduler

# Next run times
expr = CronExpression.parse("*/20 9-17 * * MON-FRI")
expr.next_runs(datetime(2024, 3, 1, 8, 0), 3)
# [2024-03-01 09:00, 2024-03-01 09:20, 2024-03-01 09:40]

# Jobs, driven by a clock you control
now = datetime(2024, 3, 1, 8, 59)
scheduler = Scheduler(clock=lambda: now)
scheduler.add("report", "0 9 * * MON-FRI", lambda: print("report sent"))
scheduler.add("broken", "*/30 * * * *", lambda: 1 / 0)

scheduler.run_due()            # [] - nothing is due at 08:59
now += timedelta(minutes=1)
scheduler.run_due()            # prints "report sent", returns ['report', 'broken']
scheduler.run_due()            # [] - same minute, nothing runs twice

job = scheduler.get("broken")
job.last_run, job.error_count, job.last_error.message
# (datetime(2024, 3, 1, 9, 0), 1, 'division by zero')
```

For a real process, `scheduler.run_forever(poll_seconds=1.0)` calls `run_due()` in a loop with `time.sleep`.

Command line:

```
$ python -m taskscheduler "*/20 9-17 * * MON-FRI" -n 3 --after 2024-03-01T08:00
2024-03-01 09:00 Fri
2024-03-01 09:20 Fri
2024-03-01 09:40 Fri
```

Supported syntax: `*`, single values, lists (`1,15`), ranges (`1-5`), steps (`*/10`, `10-40/10`), month names (`JAN`..`DEC`) and weekday names (`SUN`..`SAT`) in any letter case. In day-of-week, both `0` and `7` mean Sunday and a range may end in `SUN` (`FRI-SUN`).

## Running the tests

```
python -m pytest -q
```

The tests need only `pytest`. They use fake clocks and fixed dates, never sleep, and do not depend on the current date.

## Limitations

- **Naive datetimes only.** Time zones and daylight saving are not modeled; timezone-aware values are rejected with an error rather than silently misread. If DST matters, run the scheduler on UTC.
- **Minute resolution.** There is no seconds field, and no `@daily`-style macros or `L`, `W`, `#`, `?` extensions; they are rejected as invalid.
- **Wrap-around ranges (`FRI-MON`) and steps on a single value (`5/15`) are rejected** with a message rather than guessed at.
- **Synchronous and single-threaded.** Jobs run one after another on the calling thread, so a slow job delays the ones after it, and the scheduler is not thread-safe. Nothing is persisted across restarts.
- **No catch-up.** Minutes missed while the scheduler was not being polled produce one late run, not one run per missed minute. A job added during a minute it matches is due immediately.

Built with AI coding assistance.
