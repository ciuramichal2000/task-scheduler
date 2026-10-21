import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inspect
import time
from datetime import datetime, timedelta, timezone

import pytest

from taskscheduler import CronParseError, Scheduler


def dt(*args):
    return datetime(*args)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class Counter:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def test_job_runs_only_when_its_minute_arrives():
    clock = FakeClock(dt(2024, 1, 1, 9, 59))
    scheduler = Scheduler(clock)
    counter = Counter()
    job = scheduler.add("daily", "0 10 * * *", counter)

    assert job.next_due == dt(2024, 1, 1, 10, 0)
    assert scheduler.run_due() == []
    assert counter.calls == 0

    clock.advance(minutes=1)
    assert scheduler.run_due() == ["daily"]
    assert counter.calls == 1
    assert job.next_due == dt(2024, 1, 2, 10, 0)


def test_each_job_follows_its_own_schedule():
    clock = FakeClock(dt(2024, 1, 1, 9, 59))
    scheduler = Scheduler(clock)
    scheduler.add("hourly", "0 * * * *", Counter())
    scheduler.add("quarter-hour", "*/15 * * * *", Counter())
    scheduler.add("noon", "0 12 * * *", Counter())

    assert scheduler.run_due() == []  # 09:59: none of them
    clock.now = dt(2024, 1, 1, 10, 0)
    assert scheduler.run_due() == ["hourly", "quarter-hour"]
    clock.now = dt(2024, 1, 1, 10, 15)
    assert scheduler.run_due() == ["quarter-hour"]
    clock.now = dt(2024, 1, 1, 10, 16)
    assert scheduler.run_due() == []
    clock.now = dt(2024, 1, 1, 12, 0)
    assert scheduler.run_due() == ["hourly", "quarter-hour", "noon"]
    assert scheduler.get("noon").next_due == dt(2024, 1, 2, 12, 0)


def test_day_of_month_or_day_of_week_rule_drives_the_scheduler():
    # "the 13th, or any Friday": 6 Sep 2024 is a Friday, 7 Sep a Saturday, 13 Sep a Friday.
    clock = FakeClock(dt(2024, 9, 5, 23, 59))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("midnight", "0 0 13 * FRI", counter)

    clock.now = dt(2024, 9, 6, 0, 0)
    assert scheduler.run_due() == ["midnight"]
    clock.now = dt(2024, 9, 7, 0, 0)
    assert scheduler.run_due() == []
    clock.now = dt(2024, 9, 13, 0, 0)
    assert scheduler.run_due() == ["midnight"]
    assert counter.calls == 2


def test_job_does_not_run_twice_in_the_same_minute():
    clock = FakeClock(dt(2024, 1, 1, 10, 0, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("every-minute", "* * * * *", counter)

    for _ in range(3):  # 10:00:00, 10:00:20, 10:00:40
        scheduler.run_due()
        clock.advance(seconds=20)
    assert counter.calls == 1

    clock.now = dt(2024, 1, 1, 10, 0, 59)
    assert scheduler.run_due() == []
    assert counter.calls == 1

    clock.now = dt(2024, 1, 1, 10, 1, 0)
    assert scheduler.run_due() == ["every-minute"]
    assert counter.calls == 2


def test_last_run_records_the_clock_reading_and_counts_runs():
    clock = FakeClock(dt(2024, 1, 1, 9, 0))
    scheduler = Scheduler(clock)
    job = scheduler.add("daily", "0 10 * * *", Counter())
    assert job.last_run is None
    assert job.run_count == 0

    clock.now = dt(2024, 1, 1, 10, 0, 25)
    scheduler.run_due()

    assert job.last_run == dt(2024, 1, 1, 10, 0, 25)
    assert job.run_count == 1
    assert job.last_error is None


def test_failing_job_is_recorded_and_does_not_stop_other_jobs():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    good = Counter()

    def broken():
        raise ValueError("boom")

    bad_job = scheduler.add("broken", "* * * * *", broken)
    good_job = scheduler.add("good", "* * * * *", good)

    assert scheduler.run_due() == ["broken", "good"]
    assert good.calls == 1
    assert bad_job.last_run == dt(2024, 1, 1, 10, 0)
    assert bad_job.run_count == 1
    assert bad_job.error_count == 1
    assert bad_job.last_error.error_type == "ValueError"
    assert bad_job.last_error.message == "boom"
    assert bad_job.last_error.when == dt(2024, 1, 1, 10, 0)
    assert good_job.error_count == 0

    # A failed job is not retried within the same minute...
    assert scheduler.run_due() == []
    # ...but runs again, and fails again, in the next one.
    clock.advance(minutes=1)
    scheduler.run_due()
    assert bad_job.error_count == 2
    assert [e.when for e in bad_job.errors] == [dt(2024, 1, 1, 10, 0), dt(2024, 1, 1, 10, 1)]


def test_success_after_a_failure_clears_last_error_but_keeps_history():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    outcomes = [RuntimeError("first try"), None]

    def flaky():
        problem = outcomes.pop(0)
        if problem:
            raise problem

    job = scheduler.add("flaky", "* * * * *", flaky)

    scheduler.run_due()
    assert job.last_error.message == "first try"

    clock.advance(minutes=1)
    scheduler.run_due()
    assert job.last_error is None
    assert job.error_count == 1
    assert len(job.errors) == 1
    assert job.run_count == 2


def test_error_history_keeps_only_the_most_recent_failures():
    clock = FakeClock(dt(2024, 1, 1, 0, 0))
    scheduler = Scheduler(clock)

    def always_fails():
        raise OSError("disk")

    job = scheduler.add("fails", "* * * * *", always_fails)
    for _ in range(60):  # one run per minute, 00:00 .. 00:59
        scheduler.run_due()
        clock.advance(minutes=1)

    assert job.error_count == 60
    assert len(job.errors) == 50
    assert job.errors[0].when == dt(2024, 1, 1, 0, 10)  # runs 00:00-00:09 were dropped
    assert job.errors[-1].when == dt(2024, 1, 1, 0, 59)


def test_missed_minutes_are_coalesced_into_one_late_run():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    job = scheduler.add("every-minute", "* * * * *", counter)
    scheduler.run_due()

    clock.now = dt(2024, 1, 1, 10, 5, 30)  # five scheduled minutes pass unobserved
    assert scheduler.run_due() == ["every-minute"]

    assert counter.calls == 2  # not 6
    assert job.next_due == dt(2024, 1, 1, 10, 6)


def test_a_job_added_during_its_scheduled_minute_is_due_immediately():
    clock = FakeClock(dt(2024, 1, 1, 10, 0, 45))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("ten-o-clock", "0 10 * * *", counter)

    assert scheduler.run_due() == ["ten-o-clock"]


def test_a_job_added_after_its_scheduled_minute_waits_for_the_next_day():
    clock = FakeClock(dt(2024, 1, 1, 10, 1, 0))
    scheduler = Scheduler(clock)
    job = scheduler.add("ten-o-clock", "0 10 * * *", Counter())

    assert job.next_due == dt(2024, 1, 2, 10, 0)
    assert scheduler.run_due() == []


def test_clock_moving_backwards_does_not_rerun_a_job():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("every-minute", "* * * * *", counter)
    scheduler.run_due()

    clock.now = dt(2024, 1, 1, 9, 58, 30)  # e.g. an NTP correction
    assert scheduler.run_due() == []
    clock.now = dt(2024, 1, 1, 10, 0, 30)  # back in the minute that already ran
    assert scheduler.run_due() == []

    assert counter.calls == 1


def test_a_large_backwards_jump_pauses_the_job_until_its_old_schedule_is_reached():
    # Documented limitation: next_due is not recomputed when the clock goes back,
    # so a week-long jump back skips a week of runs rather than repeating any.
    clock = FakeClock(dt(2024, 1, 10, 10, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    job = scheduler.add("daily", "0 10 * * *", counter)
    scheduler.run_due()
    assert job.next_due == dt(2024, 1, 11, 10, 0)

    clock.now = dt(2024, 1, 3, 10, 0)
    assert scheduler.run_due() == []
    assert job.next_due == dt(2024, 1, 11, 10, 0)
    assert counter.calls == 1


def test_jobs_run_in_registration_order():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    for name in ["c", "a", "b"]:
        scheduler.add(name, "* * * * *", Counter())

    assert scheduler.run_due() == ["c", "a", "b"]
    assert [job.name for job in scheduler.jobs] == ["c", "a", "b"]


def test_job_removed_by_an_earlier_job_in_the_same_pass_does_not_run():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    victim = Counter()
    scheduler.add("killer", "* * * * *", lambda: scheduler.remove("victim"))
    scheduler.add("victim", "* * * * *", victim)

    assert scheduler.run_due() == ["killer"]
    assert victim.calls == 0


def test_removed_job_no_longer_runs_and_unknown_names_raise():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("temp", "* * * * *", counter)

    scheduler.remove("temp")
    assert scheduler.run_due() == []
    assert counter.calls == 0
    with pytest.raises(KeyError, match="temp"):
        scheduler.remove("temp")
    with pytest.raises(KeyError, match="nope"):
        scheduler.get("nope")


def test_invalid_registrations_are_rejected_and_leave_nothing_behind():
    scheduler = Scheduler(FakeClock(dt(2024, 1, 1, 10, 0)))
    scheduler.add("taken", "* * * * *", Counter())

    with pytest.raises(ValueError, match="already registered"):
        scheduler.add("taken", "* * * * *", Counter())
    with pytest.raises(ValueError, match="non-empty"):
        scheduler.add("   ", "* * * * *", Counter())
    with pytest.raises(CronParseError, match="minute: 61 is out of range"):
        scheduler.add("bad-cron", "61 * * * *", Counter())
    with pytest.raises(TypeError, match="callable"):
        scheduler.add("not-callable", "* * * * *", "echo hi")

    assert [job.name for job in scheduler.jobs] == ["taken"]


def test_clock_returning_an_aware_datetime_is_rejected_with_a_clear_message():
    scheduler = Scheduler(lambda: datetime(2024, 1, 1, tzinfo=timezone.utc))

    with pytest.raises(ValueError, match="clock.*naive"):
        scheduler.add("job", "* * * * *", Counter())


def test_job_is_retired_when_the_calendar_has_no_further_run():
    clock = FakeClock(dt(9999, 12, 31, 23, 59))
    scheduler = Scheduler(clock)
    counter = Counter()
    job = scheduler.add("last-ever", "* * * * *", counter)

    assert scheduler.run_due() == ["last-ever"]
    assert job.next_due is None

    clock.advance(seconds=30)
    assert scheduler.run_due() == []
    assert counter.calls == 1


def test_job_with_no_matching_minute_left_has_no_next_due_from_the_start():
    scheduler = Scheduler(FakeClock(dt(9999, 6, 1)))
    counter = Counter()
    job = scheduler.add("new-year", "0 0 1 1 *", counter)

    assert job.next_due is None
    assert scheduler.run_due() == []
    assert counter.calls == 0


def test_scheduler_uses_local_wall_clock_time_by_default():
    # Not a test of the current date: only that the default clock reads local time,
    # which an "every minute" job turns into next_due without any sleeping.
    job = Scheduler().add("now", "* * * * *", Counter())

    assert abs(job.next_due - datetime.now()) < timedelta(minutes=2)


def test_run_forever_defaults_are_one_second_and_real_sleep():
    parameters = inspect.signature(Scheduler.run_forever).parameters

    assert parameters["poll_seconds"].default == 1.0
    assert parameters["sleep"].default is time.sleep


def test_keyboard_interrupt_is_not_swallowed_by_error_recording():
    scheduler = Scheduler(FakeClock(dt(2024, 1, 1, 10, 0)))

    def interrupted():
        raise KeyboardInterrupt

    job = scheduler.add("interrupted", "* * * * *", interrupted)

    with pytest.raises(KeyboardInterrupt):
        scheduler.run_due()
    assert job.error_count == 0


def test_run_forever_polls_and_sleeps_without_real_waiting():
    clock = FakeClock(dt(2024, 1, 1, 10, 0, 0))
    scheduler = Scheduler(clock)
    counter = Counter()
    scheduler.add("every-minute", "* * * * *", counter)
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds=seconds)

    scheduler.run_forever(20, sleep=fake_sleep, should_stop=lambda: len(slept) >= 10)

    # Passes happen at 0:00, 0:20, ..., 3:00 (10 of them); the job is due at 0:00, 1:00, 2:00, 3:00.
    assert slept == [20] * 10
    assert counter.calls == 4
    assert clock.now == dt(2024, 1, 1, 10, 3, 20)


def test_run_forever_runs_due_jobs_before_it_sleeps_and_checks_for_stop_before_each_pass():
    clock = FakeClock(dt(2024, 1, 1, 10, 0))
    scheduler = Scheduler(clock)
    events = []
    scheduler.add("job", "* * * * *", lambda: events.append("run"))
    stop_answers = iter([False, False, True])

    scheduler.run_forever(
        30, sleep=lambda seconds: events.append("sleep"), should_stop=lambda: next(stop_answers)
    )

    # The clock never moves here, so the second pass finds nothing due.
    assert events == ["run", "sleep", "sleep"]

    events.clear()
    scheduler.run_forever(30, sleep=lambda seconds: events.append("sleep"), should_stop=lambda: True)
    assert events == []  # asked to stop before the first pass: no pass, no sleep


@pytest.mark.parametrize("bad", [0, -1, float("nan")])
def test_run_forever_rejects_non_positive_poll_interval(bad):
    scheduler = Scheduler(FakeClock(dt(2024, 1, 1)))

    with pytest.raises(ValueError, match="poll_seconds"):
        scheduler.run_forever(bad, sleep=lambda s: None, should_stop=lambda: True)
