"""A small cron-driven job scheduler with an injectable clock.

The scheduler never sleeps by itself. Callers drive it by calling
:meth:`Scheduler.run_due` (or :meth:`Scheduler.run_forever`, which takes the
sleep function as a parameter), so tests can replace both the clock and the
sleep and run a simulated day in microseconds.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .cron import CronExpression, ensure_naive_datetime
from .errors import NoNextRunError

Clock = Callable[[], datetime]
JobFunc = Callable[[], object]

# A job that fails every minute would otherwise grow its history without bound.
ERROR_HISTORY_LIMIT = 50


@dataclass(frozen=True)
class JobError:
    """One failed run: when the clock said it happened, and what was raised."""

    when: datetime
    error_type: str
    message: str


@dataclass
class Job:
    """A registered job and what the scheduler has recorded about it.

    Treat the fields as read-only; the scheduler owns them.

    ``next_due`` is the earliest scheduled minute that has not been run yet
    (``None`` once the calendar has no further matching minute). ``last_run``
    is the clock reading when the most recent run started, whether it
    succeeded or not. ``last_error`` describes the most recent run only and is
    ``None`` if that run succeeded; ``errors`` keeps recent failures.
    """

    name: str
    schedule: CronExpression
    func: JobFunc = field(repr=False)
    next_due: datetime | None
    last_run: datetime | None = None
    last_error: JobError | None = None
    run_count: int = 0
    error_count: int = 0
    errors: deque[JobError] = field(default_factory=lambda: deque(maxlen=ERROR_HISTORY_LIMIT))


def _next_or_none(schedule: CronExpression, after: datetime) -> datetime | None:
    try:
        return schedule.next_after(after)
    except NoNextRunError:
        return None


def _first_due(schedule: CronExpression, now: datetime) -> datetime | None:
    """First minute to run for a job registered at ``now``.

    The current minute counts: a job added at 10:00:45 with ``0 10 * * *`` is
    due immediately, because minutes are the scheduler's smallest unit and
    10:00 is the minute we are in.
    """
    current_minute = now.replace(second=0, microsecond=0)
    if schedule.matches(current_minute):
        return current_minute
    return _next_or_none(schedule, current_minute)


def _never_stop() -> bool:
    return False


class Scheduler:
    """Registers named cron jobs and runs the ones that are due.

    Not thread-safe: jobs run one after another on the thread that calls
    :meth:`run_due`, in registration order.
    """

    def __init__(self, clock: Clock = datetime.now) -> None:
        self._clock = clock
        self._jobs: dict[str, Job] = {}

    def _now(self) -> datetime:
        now = self._clock()
        ensure_naive_datetime(now, "clock() result")
        return now

    @property
    def jobs(self) -> list[Job]:
        """Registered jobs in registration order."""
        return list(self._jobs.values())

    def get(self, name: str) -> Job:
        """The job registered as ``name``; KeyError if there is none."""
        try:
            return self._jobs[name]
        except KeyError:
            raise KeyError(f"no job named {name!r}") from None

    def add(self, name: str, expression: str, func: JobFunc) -> Job:
        """Register ``func`` under ``name`` to run whenever ``expression`` fires.

        Nothing is registered if the name, the expression or the function is
        invalid, so a typo never leaves a half-configured job behind.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("job name must be a non-empty string")
        if name in self._jobs:
            raise ValueError(f"a job named {name!r} is already registered")
        if not callable(func):
            raise TypeError(f"job {name!r}: func must be callable, got {type(func).__name__}")
        schedule = CronExpression.parse(expression)
        job = Job(name=name, schedule=schedule, func=func, next_due=_first_due(schedule, self._now()))
        self._jobs[name] = job
        return job

    def remove(self, name: str) -> None:
        """Unregister ``name``; KeyError if there is no such job."""
        self.get(name)
        del self._jobs[name]

    def run_due(self) -> list[str]:
        """Run every job whose scheduled minute has arrived; return their names.

        The clock is read once, so all jobs in one call see the same "now".
        A job is advanced to its next minute *before* it runs. That is what
        keeps it from running twice in the same minute, however often this is
        called, and it also means a clock that jumps forward over several
        scheduled minutes yields one late run rather than a burst of catch-up
        runs. An exception raised by a job is recorded on that job and never
        stops the others; only ``Exception`` is caught, so Ctrl+C still works.
        """
        now = self._now()
        ran: list[str] = []
        for job in self.jobs:
            if self._jobs.get(job.name) is not job:
                continue  # removed by a job that ran earlier in this call
            if job.next_due is None or job.next_due > now:
                continue
            job.last_run = now
            job.run_count += 1
            job.next_due = _next_or_none(job.schedule, now)
            ran.append(job.name)
            try:
                job.func()
            except Exception as exc:
                failure = JobError(when=now, error_type=type(exc).__name__, message=str(exc))
                job.last_error = failure
                job.error_count += 1
                job.errors.append(failure)
            else:
                job.last_error = None
        return ran

    def run_forever(
        self,
        poll_seconds: float = 1.0,
        *,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] = _never_stop,
    ) -> None:
        """Call :meth:`run_due` every ``poll_seconds`` until ``should_stop()`` is true.

        ``sleep`` and ``should_stop`` are parameters so tests can drive the loop
        without waiting; ``should_stop`` is checked before every pass.
        """
        if not poll_seconds > 0:
            raise ValueError(f"poll_seconds must be positive, got {poll_seconds}")
        while not should_stop():
            self.run_due()
            sleep(poll_seconds)
