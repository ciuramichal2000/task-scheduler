import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import pytest

from taskscheduler import CronExpression, NoNextRunError


def dt(*args):
    return datetime(*args)


def runs(text, after, count):
    return CronExpression.parse(text).next_runs(after, count)


def test_step_minutes_after_a_mid_minute_start():
    assert runs("*/15 * * * *", dt(2024, 1, 1, 10, 7, 30), 4) == [
        dt(2024, 1, 1, 10, 15),
        dt(2024, 1, 1, 10, 30),
        dt(2024, 1, 1, 10, 45),
        dt(2024, 1, 1, 11, 0),
    ]


def test_minute_steps_restart_every_hour():
    # */7 means 0,7,...,56 within each hour, so after 56 comes :00, not :03.
    assert runs("*/7 * * * *", dt(2024, 1, 1, 10, 50), 3) == [
        dt(2024, 1, 1, 10, 56),
        dt(2024, 1, 1, 11, 0),
        dt(2024, 1, 1, 11, 7),
    ]


@pytest.mark.parametrize(
    "after, expected",
    [
        (dt(2024, 1, 1, 10, 14, 59, 999999), dt(2024, 1, 1, 10, 15)),
        (dt(2024, 1, 1, 10, 15, 0), dt(2024, 1, 2, 10, 15)),  # exactly on a run: strictly after
        (dt(2024, 1, 1, 10, 15, 30), dt(2024, 1, 2, 10, 15)),  # still inside the 10:15 minute
        (dt(2024, 1, 1, 10, 16), dt(2024, 1, 2, 10, 15)),
    ],
)
def test_next_run_is_strictly_after_the_given_moment(after, expected):
    assert CronExpression.parse("15 10 * * *").next_after(after) == expected


def test_hour_step_rolls_over_midnight():
    assert runs("0 */6 * * *", dt(2024, 1, 1, 23, 0), 4) == [
        dt(2024, 1, 2, 0, 0),
        dt(2024, 1, 2, 6, 0),
        dt(2024, 1, 2, 12, 0),
        dt(2024, 1, 2, 18, 0),
    ]


def test_rolls_over_the_year():
    assert runs("0 0 1 1 *", dt(2024, 12, 31, 23, 59, 59), 1) == [dt(2025, 1, 1, 0, 0)]
    assert runs("30 23 31 12 *", dt(2024, 1, 1), 2) == [
        dt(2024, 12, 31, 23, 30),
        dt(2025, 12, 31, 23, 30),
    ]


def test_day_31_skips_months_that_are_too_short():
    # Only Jan, Mar, May, Jul, Aug, Oct, Dec have a 31st.
    assert runs("0 0 31 * *", dt(2024, 1, 1), 8) == [
        dt(2024, 1, 31),
        dt(2024, 3, 31),
        dt(2024, 5, 31),
        dt(2024, 7, 31),
        dt(2024, 8, 31),
        dt(2024, 10, 31),
        dt(2024, 12, 31),
        dt(2025, 1, 31),
    ]


def test_leap_day_runs_only_in_leap_years_and_century_rule_is_respected():
    leap_day = CronExpression.parse("0 12 29 2 *")

    assert leap_day.next_after(dt(2024, 2, 28)) == dt(2024, 2, 29, 12, 0)
    assert leap_day.next_after(dt(2024, 2, 29, 12, 0)) == dt(2028, 2, 29, 12, 0)
    # 2100 is divisible by 100 but not by 400, so it is not a leap year.
    assert leap_day.next_runs(dt(2096, 3, 1), 2) == [dt(2104, 2, 29, 12, 0), dt(2108, 2, 29, 12, 0)]


def test_day_of_month_or_day_of_week_when_both_are_restricted():
    # "the 13th, or any Friday". 6 Sep 2024 is a Friday; 13 Sep is both; 13 Oct is a Sunday.
    assert runs("0 0 13 * FRI", dt(2024, 9, 1), 7) == [
        dt(2024, 9, 6),
        dt(2024, 9, 13),
        dt(2024, 9, 20),
        dt(2024, 9, 27),
        dt(2024, 10, 4),
        dt(2024, 10, 11),
        dt(2024, 10, 13),
    ]


def test_day_of_month_alone_is_not_widened_to_every_weekday():
    assert runs("0 0 13 * *", dt(2024, 9, 1), 4) == [
        dt(2024, 9, 13),
        dt(2024, 10, 13),
        dt(2024, 11, 13),
        dt(2024, 12, 13),
    ]


def test_starred_step_in_day_of_month_is_anded_with_day_of_week():
    # "*/2" starts with '*', so the AND rule applies: odd days that are also Mondays.
    # Mondays: Jan 1, 8, 15, 22, 29 and Feb 5, 12, 19, 26 in 2024; keep the odd dates.
    assert runs("0 0 */2 * MON", dt(2023, 12, 31, 12, 0), 5) == [
        dt(2024, 1, 1),
        dt(2024, 1, 15),
        dt(2024, 1, 29),
        dt(2024, 2, 5),
        dt(2024, 2, 19),
    ]


def test_weekday_range_skips_the_weekend():
    # 1 Mar 2024 is a Friday, so after the 10:00 run the next weekday slots are Mon-Wed.
    assert runs("30 9 * * MON-FRI", dt(2024, 3, 1, 10, 0), 3) == [
        dt(2024, 3, 4, 9, 30),
        dt(2024, 3, 5, 9, 30),
        dt(2024, 3, 6, 9, 30),
    ]
    assert runs("30 9 * * MON-FRI", dt(2024, 3, 1, 9, 0), 1) == [dt(2024, 3, 1, 9, 30)]


def test_seven_means_sunday():
    # 1 Jan 2024 is a Monday, so the first Sunday is the 7th.
    assert runs("0 0 * * 7", dt(2024, 1, 1), 2) == [dt(2024, 1, 7), dt(2024, 1, 14)]


def test_month_names_select_months():
    assert runs("0 6 1 JAN,JUL *", dt(2024, 1, 1, 6, 0), 3) == [
        dt(2024, 7, 1, 6, 0),
        dt(2025, 1, 1, 6, 0),
        dt(2025, 7, 1, 6, 0),
    ]


def test_or_rule_with_a_day_that_never_exists_still_fires_on_weekdays():
    # Day 31 does not exist in February, so only the Mondays of February remain.
    assert runs("0 0 31 2 MON", dt(2024, 1, 31), 5) == [
        dt(2024, 2, 5),
        dt(2024, 2, 12),
        dt(2024, 2, 19),
        dt(2024, 2, 26),
        dt(2025, 2, 3),
    ]


def test_a_thousand_runs_are_consecutive_minutes():
    result = runs("* * * * *", dt(2024, 1, 1, 0, 0), 1000)

    assert len(result) == 1000
    assert all(later - earlier == timedelta(minutes=1) for earlier, later in zip(result, result[1:]))
    assert result[0] == dt(2024, 1, 1, 0, 1)
    assert result[-1] == dt(2024, 1, 1, 16, 40)  # 1000 minutes = 16 h 40 min


def test_count_zero_is_empty_and_bad_counts_are_rejected():
    expr = CronExpression.parse("* * * * *")

    assert expr.next_runs(dt(2024, 1, 1), 0) == []
    with pytest.raises(ValueError, match="negative"):
        expr.next_runs(dt(2024, 1, 1), -1)
    with pytest.raises(TypeError, match="count"):
        expr.next_runs(dt(2024, 1, 1), 2.5)


def test_timezone_aware_and_non_datetime_inputs_are_rejected():
    expr = CronExpression.parse("* * * * *")

    with pytest.raises(ValueError, match="naive"):
        expr.next_after(datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="naive"):
        expr.matches(datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(TypeError, match="datetime"):
        expr.next_after("2024-01-01")


def test_calendar_end_raises_and_next_runs_returns_what_exists():
    every_minute = CronExpression.parse("* * * * *")

    with pytest.raises(NoNextRunError, match="9999"):
        every_minute.next_after(dt(9999, 12, 31, 23, 59))
    assert every_minute.next_runs(dt(9999, 12, 31, 23, 57), 5) == [
        dt(9999, 12, 31, 23, 58),
        dt(9999, 12, 31, 23, 59),
    ]


def test_matches_ignores_seconds_and_checks_every_field():
    expr = CronExpression.parse("30 9 * * FRI")

    assert expr.matches(dt(2024, 3, 1, 9, 30, 45))  # Friday
    assert not expr.matches(dt(2024, 3, 2, 9, 30))  # Saturday
    assert not expr.matches(dt(2024, 3, 1, 9, 31))
    assert not expr.matches(dt(2024, 3, 1, 10, 30))


def test_matches_checks_the_month_hour_minute_and_day_of_month_separately():
    expr = CronExpression.parse("45 6 15 JUN *")

    assert expr.matches(dt(2024, 6, 15, 6, 45))
    assert expr.matches(dt(2025, 6, 15, 6, 45))  # the year does not matter
    assert not expr.matches(dt(2024, 7, 15, 6, 45))  # only the month differs
    assert not expr.matches(dt(2024, 6, 16, 6, 45))  # only the day differs
    assert not expr.matches(dt(2024, 6, 15, 7, 45))  # only the hour differs
    assert not expr.matches(dt(2024, 6, 15, 6, 46))  # only the minute differs


def test_sunday_only_range_fires_on_sundays_and_not_on_other_days():
    # 1 Jan 2024 is a Monday; SUN-SUN must not behave like "every day".
    assert runs("0 0 * * SUN-SUN", dt(2024, 1, 1), 3) == [dt(2024, 1, 7), dt(2024, 1, 14), dt(2024, 1, 21)]


def test_saturday_to_sunday_range_covers_exactly_the_weekend():
    # Saturday 6 Jan and Sunday 7 Jan, then Saturday 13 Jan.
    assert runs("0 0 * * SAT-SUN", dt(2024, 1, 1), 3) == [dt(2024, 1, 6), dt(2024, 1, 7), dt(2024, 1, 13)]


def test_no_run_left_in_the_last_year_of_the_calendar_raises_naming_that_year():
    new_year = CronExpression.parse("0 0 1 1 *")

    with pytest.raises(NoNextRunError, match="through year 9999"):
        new_year.next_after(dt(9999, 1, 1))
    assert new_year.next_runs(dt(9999, 1, 1), 3) == []
    assert new_year.next_after(dt(9998, 1, 1)) == dt(9999, 1, 1)


def test_a_weekday_that_a_date_only_rarely_falls_on_is_still_found():
    # AND rule: the 29th of February that is also a Sunday (2004, 2032, ...).
    assert runs("0 0 29 2 */7", dt(2024, 1, 1), 2) == [dt(2032, 2, 29), dt(2060, 2, 29)]


def test_matches_uses_the_or_rule_for_day_fields():
    expr = CronExpression.parse("0 0 13 * FRI")

    assert expr.matches(dt(2024, 10, 11))  # Friday, not the 13th
    assert expr.matches(dt(2024, 10, 13))  # Sunday the 13th
    assert not expr.matches(dt(2024, 10, 14))  # Monday the 14th
