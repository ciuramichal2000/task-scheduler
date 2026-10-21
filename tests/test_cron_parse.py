import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from taskscheduler import CronExpression, CronParseError


def test_star_expands_to_every_value_of_each_field():
    expr = CronExpression.parse("* * * * *")

    assert expr.minutes == tuple(range(0, 60))
    assert expr.hours == tuple(range(0, 24))
    assert expr.days_of_month == tuple(range(1, 32))
    assert expr.months == tuple(range(1, 13))
    assert expr.days_of_week == tuple(range(0, 7))


def test_lists_ranges_and_steps_are_expanded():
    expr = CronExpression.parse("0,15,30,45 8-10 1-15/7 */4 */2")

    assert expr.minutes == (0, 15, 30, 45)
    assert expr.hours == (8, 9, 10)
    assert expr.days_of_month == (1, 8, 15)  # 1, 1+7, 1+14
    assert expr.months == (1, 5, 9)  # step 4 starting at the first month
    assert expr.days_of_week == (0, 2, 4, 6)


def test_overlapping_items_are_deduplicated_and_sorted():
    assert CronExpression.parse("5,1-6,3 * * * *").minutes == (1, 2, 3, 4, 5, 6)


def test_step_applies_to_ranges_and_may_exceed_the_field_size():
    assert CronExpression.parse("10-40/10 * * * *").minutes == (10, 20, 30, 40)
    assert CronExpression.parse("*/45 * * * *").minutes == (0, 45)
    assert CronExpression.parse("*/90 * * * *").minutes == (0,)


def test_month_and_weekday_names_are_case_insensitive():
    expr = CronExpression.parse("0 0 * jan,Mar-MAY mon-fri")

    assert expr.months == (1, 3, 4, 5)
    assert expr.days_of_week == (1, 2, 3, 4, 5)


def test_names_work_with_steps():
    assert CronExpression.parse("0 0 * JAN-DEC/6 *").months == (1, 7)
    assert CronExpression.parse("0 0 * * MON-FRI/2").days_of_week == (1, 3, 5)


def test_sunday_can_be_written_as_0_7_or_sun():
    spellings = ["0", "7", "SUN", "sun"]

    assert [CronExpression.parse(f"* * * * {s}").days_of_week for s in spellings] == [(0,)] * 4


def test_weekday_range_may_end_on_sunday():
    assert CronExpression.parse("* * * * FRI-SUN").days_of_week == (0, 5, 6)
    assert CronExpression.parse("* * * * 5-7").days_of_week == (0, 5, 6)


@pytest.mark.parametrize("spelling", ["SUN-SUN", "sun-sun", "0-SUN", "0-0", "7-7", "SUN"])
def test_a_range_that_starts_and_ends_on_sunday_is_sunday_only(spelling):
    # Regression: the "Sunday ends the week as 7" rule used to widen SUN-SUN to every day.
    assert CronExpression.parse(f"* * * * {spelling}").days_of_week == (0,)


def test_week_ranges_around_sunday():
    assert CronExpression.parse("* * * * SAT-SUN").days_of_week == (0, 6)
    assert CronExpression.parse("* * * * SUN-SAT").days_of_week == tuple(range(7))
    assert CronExpression.parse("* * * * 0-7").days_of_week == tuple(range(7))
    assert CronExpression.parse("* * * * SUN-MON").days_of_week == (0, 1)


@pytest.mark.parametrize("text, field, expected", [
    ("5-5 * * * *", "minutes", (5,)),
    ("* 0-0 * * *", "hours", (0,)),
    ("* * 31-31 * *", "days_of_month", (31,)),
    ("* * * DEC-DEC *", "months", (12,)),
])
def test_a_range_of_one_value_is_allowed(text, field, expected):
    assert getattr(CronExpression.parse(text), field) == expected


def test_extra_whitespace_is_ignored_and_equivalent_spellings_are_equal():
    messy = CronExpression.parse("  */30\t*  *   * *  ")
    plain = CronExpression.parse("0,30 * * * *")

    assert messy == plain
    assert messy.source == "*/30 * * * *"


def test_day_fields_starting_with_star_are_unrestricted_for_the_or_rule():
    assert not CronExpression.parse("* * * * 1").dom_restricted
    assert not CronExpression.parse("* * */2 * 1").dom_restricted
    assert CronExpression.parse("* * 13 * *").dom_restricted
    assert not CronExpression.parse("* * 13 * *").dow_restricted
    assert CronExpression.parse("* * * * FRI").dow_restricted


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("* * * *", "expected 5 fields"),
        ("* * * * * *", "got 6"),
        ("@daily", "macro '@daily'"),
        ("60 * * * *", "minute: 60 is out of range 0-59"),
        ("* 24 * * *", "hour: 24 is out of range 0-23"),
        ("* * 0 * *", "day-of-month: 0 is out of range 1-31"),
        ("* * 32 * *", "day-of-month: 32 is out of range 1-31"),
        ("* * * 0 *", "month: 0 is out of range 1-12"),
        ("* * * 13 *", "month: 13 is out of range 1-12"),
        ("* * * * 8", "day-of-week: 8 is out of range 0-7"),
        ("*/0 * * * *", "step '0' in '*/0' must be a positive whole number"),
        ("*/x * * * *", "step 'x'"),
        ("*/ * * * *", "step ''"),
        ("*/5/2 * * * *", "step '5/2'"),
        ("5-1 * * * *", "range '5-1' is descending"),
        ("* * * * FRI-MON", "range 'FRI-MON' is descending"),
        ("1,,2 * * * *", "minute: empty item in list '1,,2'"),
        ("1, * * * *", "empty item"),
        ("abc * * * *", "minute: invalid value 'abc'"),
        ("* * * FOO *", "month: invalid value 'FOO'"),
        ("* * * * MONDAY", "day-of-week: invalid value 'MONDAY'"),
        ("* * * * JAN", "day-of-week: invalid value 'JAN'"),
        ("? * * * *", "invalid value '?'"),
        ("5/15 * * * *", "step in '5/15' needs '*' or a range"),
        ("-5 * * * *", "incomplete range '-5'"),
        ("1- * * * *", "incomplete range '1-'"),
        ("1-2-3 * * * *", "invalid value '2-3'"),
        ("٣ * * * *", "invalid value"),  # Arabic-Indic digit three is not a cron digit
        # str.upper() turns these into valid names (SUN, SEP, FRI); they must still be refused.
        ("* * * * ſun", "day-of-week: invalid value"),
        ("0 0 1 ſep *", "month: invalid value"),
        ("* * * * FRı", "day-of-week: invalid value"),
        ("L * * * *", "invalid value 'L'"),
        ("1#2 * * * *", "invalid value '1#2'"),
        ("+5 * * * *", "invalid value '+5'"),
        ("-1 * * * *", "incomplete range '-1'"),
        ("1.5 * * * *", "invalid value '1.5'"),
        ("1000000 * * * *", "invalid value"),
        ("0 0 30 2 *", "can never run"),
        ("0 0 31 4,6,9,11 *", "can never run"),
    ],
)
def test_invalid_expressions_are_rejected_with_a_message_naming_the_problem(text, fragment):
    with pytest.raises(CronParseError) as info:
        CronExpression.parse(text)

    assert fragment in str(info.value)


def test_enormous_numbers_are_a_parse_error_not_a_crash():
    with pytest.raises(CronParseError, match="invalid value"):
        CronExpression.parse("9" * 5000 + " * * * *")


@pytest.mark.parametrize("not_text", [None, 5, b"* * * * *", ["*"] * 5])
def test_non_string_input_is_rejected(not_text):
    with pytest.raises(CronParseError, match="must be a string"):
        CronExpression.parse(not_text)


def test_parse_error_is_also_a_value_error():
    with pytest.raises(ValueError):
        CronExpression.parse("nonsense")


def test_impossible_day_is_allowed_when_the_weekday_side_can_still_match():
    # Under the OR rule "31 Feb or any Monday" is a real schedule: Mondays in February.
    assert CronExpression.parse("0 0 31 2 MON").days_of_month == (31,)
    # Feb 29 exists in leap years, so it must not be rejected as impossible.
    assert CronExpression.parse("0 0 29 2 *").months == (2,)
