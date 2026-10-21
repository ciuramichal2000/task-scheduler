"""Exception types raised by the taskscheduler package."""


class CronParseError(ValueError):
    """A cron expression is malformed, or can never match any date.

    It subclasses ValueError so callers that only care about "bad input" can
    catch the builtin without importing this package's types.
    """


class NoNextRunError(LookupError):
    """No run time exists after the requested moment (the calendar ran out)."""
