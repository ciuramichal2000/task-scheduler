"""Parse five-field cron expressions and compute the minutes they fire on.

Grammar (the classic Vixie/POSIX one): ``minute hour day-of-month month
day-of-week``. Every field is a comma-separated list whose items are ``*``, a
single value, an ``a-b`` range, or a ``*/n`` / ``a-b/n`` step. The month and
day-of-week fields also accept three-letter English names, case-insensitive.

All datetimes are naive wall-clock values; see the README for what that means
for time zones and daylight saving.
"""

from __future__ import annotations

import calendar
import re
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import MAXYEAR, date, datetime, timedelta

from .errors import CronParseError, NoNextRunError

# Digits are matched explicitly because str.isdigit() accepts non-ASCII digits,
# and the length cap keeps int() away from absurdly long inputs.
_NUMBER = re.compile(r"[0-9]{1,6}")

# The Gregorian calendar repeats exactly every 400 years, so an expression with
# no run in that span never has one. The bound also guarantees the search ends.
SEARCH_HORIZON_YEARS = 400

# A leap year, used to ask "what is the longest this month can ever be?".
_LEAP_YEAR = 2000


@dataclass(frozen=True)
class _FieldSpec:
    """Allowed range and optional names for one cron field."""

    name: str
    low: int
    high: int
    names: Mapping[str, int] = field(default_factory=dict)
    # Day-of-week accepts 7 as a second spelling of Sunday (0).
    high_aliases_low: bool = False


_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}
_WEEKDAY_NAMES = {
    name: number for number, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))
}

_MINUTE = _FieldSpec("minute", 0, 59)
_HOUR = _FieldSpec("hour", 0, 23)
_DAY_OF_MONTH = _FieldSpec("day-of-month", 1, 31)
_MONTH = _FieldSpec("month", 1, 12, _MONTH_NAMES)
_DAY_OF_WEEK = _FieldSpec("day-of-week", 0, 7, _WEEKDAY_NAMES, high_aliases_low=True)


def ensure_naive_datetime(value: object, label: str) -> None:
    """Reject anything but a naive datetime, with a message that says why.

    Mixing aware and naive values makes Python raise a vague comparison error
    far from the cause, and silently dropping tzinfo would hide DST mistakes.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is not None:
        raise ValueError(
            f"{label} must be a naive datetime (no timezone); convert to one fixed "
            "zone such as UTC and drop tzinfo before passing it in"
        )


def _parse_step(step_text: str, item: str, spec: _FieldSpec) -> int:
    if _NUMBER.fullmatch(step_text) is None or int(step_text) < 1:
        raise CronParseError(
            f"{spec.name}: step {step_text!r} in {item!r} must be a positive whole number"
        )
    return int(step_text)


def _parse_value(token: str, spec: _FieldSpec, *, is_range_end: bool = False) -> int:
    """Turn one number or name into an in-range integer."""
    if _NUMBER.fullmatch(token):
        value = int(token)
    else:
        key = token.upper()
        # upper() maps some non-ASCII letters onto ASCII ones (the long s in
        # "ſun" becomes "SUN"), so names must be plain ASCII to count.
        if not token.isascii() or key not in spec.names:
            expected = f"a number from {spec.low} to {spec.high}"
            if spec.names:
                expected += f" or a name ({', '.join(spec.names)})"
            raise CronParseError(f"{spec.name}: invalid value {token!r}; expected {expected}")
        value = spec.names[key]
        if is_range_end and spec.high_aliases_low and value == spec.low:
            # "FRI-SUN" must mean Friday through Sunday, so Sunday ends the week as 7.
            value = spec.high
    if not spec.low <= value <= spec.high:
        raise CronParseError(f"{spec.name}: {value} is out of range {spec.low}-{spec.high}")
    return value


def _parse_item(item: str, spec: _FieldSpec) -> list[int]:
    """Expand one list item (``*``, ``5``, ``1-5``, ``*/2``, ``1-5/2``)."""
    base, slash, step_text = item.partition("/")
    step = _parse_step(step_text, item, spec) if slash else 1

    if base == "*":
        first, last = spec.low, spec.high
    elif "-" in base:
        low_text, _, high_text = base.partition("-")
        if not low_text or not high_text:
            raise CronParseError(f"{spec.name}: incomplete range {item!r}")
        first = _parse_value(low_text, spec)
        # A name for the first day of the week ends a range as 7 (FRI-SUN is
        # Friday to Sunday), unless the range also starts there: SUN-SUN and
        # 0-SUN are Sunday alone, not the whole week.
        last = _parse_value(high_text, spec, is_range_end=first > spec.low)
        if first > last:
            raise CronParseError(
                f"{spec.name}: range {item!r} is descending; wrap-around ranges are "
                "not supported, split it into a list instead"
            )
    else:
        if slash:
            raise CronParseError(
                f"{spec.name}: step in {item!r} needs '*' or a range before the '/'"
            )
        return [_parse_value(base, spec)]
    return list(range(first, last + 1, step))


def _parse_field(text: str, spec: _FieldSpec) -> tuple[int, ...]:
    values: set[int] = set()
    for item in text.split(","):
        if not item:
            raise CronParseError(f"{spec.name}: empty item in list {text!r}")
        values.update(_parse_item(item, spec))
    if spec.high_aliases_low:
        values = {spec.low if value == spec.high else value for value in values}
    return tuple(sorted(values))


def _first_at_or_above(values: tuple[int, ...], floor: int) -> int | None:
    """Smallest entry of the sorted ``values`` that is >= ``floor``."""
    index = bisect_left(values, floor)
    return values[index] if index < len(values) else None


@dataclass(frozen=True)
class CronExpression:
    """A parsed cron expression. Build one with :meth:`parse`.

    ``days_of_week`` uses 0 for Sunday through 6 for Saturday.
    ``dom_restricted`` / ``dow_restricted`` record whether the day fields were
    written with something other than a leading ``*``: that decides between
    the OR rule and the AND rule in :meth:`matches`.
    """

    source: str = field(compare=False)
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days_of_month: tuple[int, ...]
    months: tuple[int, ...]
    days_of_week: tuple[int, ...]
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, text: str) -> CronExpression:
        """Parse ``text`` or raise :class:`CronParseError` naming the bad field."""
        if not isinstance(text, str):
            raise CronParseError(f"cron expression must be a string, got {type(text).__name__}")
        parts = text.split()
        if not parts:
            raise CronParseError("cron expression is empty")
        if parts[0].startswith("@"):
            raise CronParseError(
                f"macro {parts[0]!r} is not supported; write the five fields explicitly"
            )
        if len(parts) != 5:
            raise CronParseError(
                "expected 5 fields (minute hour day-of-month month day-of-week), "
                f"got {len(parts)} in {text!r}"
            )
        minute_text, hour_text, dom_text, month_text, dow_text = parts
        expression = cls(
            source=" ".join(parts),
            minutes=_parse_field(minute_text, _MINUTE),
            hours=_parse_field(hour_text, _HOUR),
            days_of_month=_parse_field(dom_text, _DAY_OF_MONTH),
            months=_parse_field(month_text, _MONTH),
            days_of_week=_parse_field(dow_text, _DAY_OF_WEEK),
            dom_restricted=not dom_text.startswith("*"),
            dow_restricted=not dow_text.startswith("*"),
        )
        expression._reject_if_no_such_date(dom_text, month_text)
        return expression

    def _reject_if_no_such_date(self, dom_text: str, month_text: str) -> None:
        """Fail fast on schedules like ``0 0 31 2 *`` that could never fire.

        Only the AND rule can produce an empty schedule: under the OR rule the
        weekday side always matches some day.
        """
        if self.dom_restricted and self.dow_restricted:
            return
        longest_month = max(calendar.monthrange(_LEAP_YEAR, month)[1] for month in self.months)
        if self.days_of_month[0] > longest_month:
            raise CronParseError(
                f"day-of-month {dom_text!r} never occurs in month {month_text!r}, "
                f"so {self.source!r} can never run"
            )

    def _day_matches(self, day: date) -> bool:
        """Apply the day-of-month / day-of-week rule for one calendar day.

        Like Vixie cron: when both fields are restricted a day matches if
        EITHER does; when one of them starts with ``*`` both must match (the
        starred field then just restricts nothing, or little, as in ``*/2``).
        """
        in_month_days = day.day in self.days_of_month
        in_weekdays = day.isoweekday() % 7 in self.days_of_week  # isoweekday: Sunday is 7
        if self.dom_restricted and self.dow_restricted:
            return in_month_days or in_weekdays
        return in_month_days and in_weekdays

    def matches(self, when: datetime) -> bool:
        """True if the minute containing ``when`` is one the expression fires on."""
        ensure_naive_datetime(when, "when")
        return (
            when.minute in self.minutes
            and when.hour in self.hours
            and when.month in self.months
            and self._day_matches(when.date())
        )

    def _first_time_from(self, hour: int, minute: int) -> tuple[int, int] | None:
        """Earliest (hour, minute) in a day that is not before ``hour:minute``."""
        for candidate_hour in self.hours:
            if candidate_hour < hour:
                continue
            floor = minute if candidate_hour == hour else 0
            candidate_minute = _first_at_or_above(self.minutes, floor)
            if candidate_minute is not None:
                return candidate_hour, candidate_minute
        return None

    def next_after(self, after: datetime) -> datetime:
        """First matching minute strictly later than ``after``.

        Seconds are ignored: 10:15:00 and 10:15:59 both yield 10:16 or later.
        Instead of testing every minute, the search jumps over non-matching
        months, checks the days of the remaining months one by one (at most
        31 checks per month), then picks hour and minute with a binary search.
        """
        ensure_naive_datetime(after, "after")
        try:
            start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        except OverflowError:
            raise NoNextRunError(
                f"no run time after {after.isoformat(timespec='minutes')}: "
                f"the calendar ends in year {MAXYEAR}"
            ) from None

        last_year = min(start.year + SEARCH_HORIZON_YEARS, MAXYEAR)
        for year in range(start.year, last_year + 1):
            for month in self.months:
                if (year, month) < (start.year, start.month):
                    continue
                in_start_month = (year, month) == (start.year, start.month)
                first_day = start.day if in_start_month else 1
                for day in range(first_day, calendar.monthrange(year, month)[1] + 1):
                    current = date(year, month, day)
                    if not self._day_matches(current):
                        continue
                    # Only on the very first day may the clock time lower-bound the search.
                    floor = (start.hour, start.minute) if current == start.date() else (0, 0)
                    found = self._first_time_from(*floor)
                    if found is not None:
                        return datetime(year, month, day, *found)
        raise NoNextRunError(
            f"no run time for {self.source!r} after {after.isoformat(timespec='minutes')} "
            f"through year {last_year}"
        )

    def next_runs(self, after: datetime, count: int) -> list[datetime]:
        """The next ``count`` run times after ``after``, in increasing order.

        Fewer than ``count`` are returned only if the calendar ends first.
        """
        ensure_naive_datetime(after, "after")
        if not isinstance(count, int):
            raise TypeError(f"count must be an int, got {type(count).__name__}")
        if count < 0:
            raise ValueError(f"count must not be negative, got {count}")
        runs: list[datetime] = []
        cursor = after
        for _ in range(count):
            try:
                cursor = self.next_after(cursor)
            except NoNextRunError:
                break
            runs.append(cursor)
        return runs
