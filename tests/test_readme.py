import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import re
from datetime import datetime, timedelta

from taskscheduler import Scheduler

README = os.path.join(os.path.dirname(__file__), "..", "README.md")


def readme_python_block():
    with open(README, encoding="utf-8") as handle:
        match = re.search(r"```python\n(.*?)```", handle.read(), re.DOTALL)
    assert match, "README.md has no python example"
    return match.group(1)


def test_readme_example_runs_and_prints_what_the_readme_says(capsys):
    namespace = {}
    exec(compile(readme_python_block(), "README.md", "exec"), namespace)

    assert capsys.readouterr().out == "report sent\n"
    job = namespace["job"]
    assert job.name == "broken"
    assert (job.last_run, job.error_count, job.last_error.message) == (
        datetime(2024, 3, 1, 9, 0),
        1,
        "division by zero",
    )
    assert namespace["scheduler"].get("report").run_count == 1


def test_readme_example_return_values_match_the_comments():
    now = datetime(2024, 3, 1, 8, 59)
    scheduler = Scheduler(clock=lambda: now)
    scheduler.add("report", "0 9 * * MON-FRI", lambda: None)
    scheduler.add("broken", "*/30 * * * *", lambda: 1 / 0)

    assert scheduler.run_due() == []  # "nothing is due at 08:59"
    now += timedelta(minutes=1)
    assert scheduler.run_due() == ["report", "broken"]
    assert scheduler.run_due() == []  # "same minute, nothing runs twice"
