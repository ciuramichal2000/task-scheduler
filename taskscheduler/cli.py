"""Command line front end: print the next run times of a cron expression."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime

from .cron import CronExpression


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _iso_datetime(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not an ISO date-time: {text!r} (example: 2024-03-01T08:00)"
        ) from None


def main(
    argv: Sequence[str] | None = None,
    *,
    now: Callable[[], datetime] = datetime.now,
) -> int:
    """Run the CLI; ``now`` is injectable so the default start time is testable."""
    parser = argparse.ArgumentParser(
        prog="python -m taskscheduler",
        description="Print the next run times of a five-field cron expression.",
    )
    parser.add_argument(
        "expression",
        help='quoted cron expression, for example "*/15 9-17 * * MON-FRI"',
    )
    parser.add_argument(
        "-n", "--count", type=_positive_int, default=5, help="how many run times to print (default 5)"
    )
    parser.add_argument(
        "--after",
        type=_iso_datetime,
        help="ISO start time without timezone, e.g. 2024-03-01T08:00 (default: now)",
    )
    args = parser.parse_args(argv)

    try:
        schedule = CronExpression.parse(args.expression)
        start = args.after if args.after is not None else now()
        runs = schedule.next_runs(start, args.count)
    except ValueError as error:  # includes CronParseError
        parser.error(str(error))
    if not runs:
        parser.error(f"no run times after {start.isoformat(timespec='minutes')}: the calendar ends")

    for when in runs:
        print(when.strftime("%Y-%m-%d %H:%M %a"))
    return 0
