import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import subprocess
from datetime import datetime

import pytest

from taskscheduler.cli import main

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_prints_requested_number_of_runs_after_the_given_start(capsys):
    code = main(["*/20 9-17 * * MON-FRI", "-n", "3", "--after", "2024-03-01T08:00"])

    assert code == 0
    # 1 March 2024 is a Friday.
    assert capsys.readouterr().out.splitlines() == [
        "2024-03-01 09:00 Fri",
        "2024-03-01 09:20 Fri",
        "2024-03-01 09:40 Fri",
    ]


def test_default_start_is_the_injected_now_and_default_count_is_five(capsys):
    main(["0 12 * * *"], now=lambda: datetime(2024, 5, 5, 13, 0))  # a Sunday afternoon

    assert capsys.readouterr().out.splitlines() == [
        "2024-05-06 12:00 Mon",
        "2024-05-07 12:00 Tue",
        "2024-05-08 12:00 Wed",
        "2024-05-09 12:00 Thu",
        "2024-05-10 12:00 Fri",
    ]


@pytest.mark.parametrize(
    "argv, fragment",
    [
        (["61 * * * *"], "minute: 61 is out of range 0-59"),
        (["0 0 31 2 *"], "can never run"),
        (["* * * * *", "--after", "yesterday"], "not an ISO date-time"),
        (["* * * * *", "--after", "2024-03-01T08:00:00+00:00"], "naive"),
        (["* * * * *", "--after", "9999-12-31T23:59"], "calendar ends"),
        (["* * * * *", "-n", "0"], "at least 1"),
        (["* * * * *", "-n", "many"], "not a whole number"),
    ],
)
def test_bad_input_exits_with_status_2_and_a_message_on_stderr(argv, fragment, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert fragment in captured.err
    assert captured.out == ""


def test_module_entry_point_works_end_to_end():
    result = subprocess.run(
        [sys.executable, "-m", "taskscheduler", "30 6 1 * *", "-n", "2", "--after", "2024-01-15T00:00"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    # 1 February 2024 is a Thursday, 1 March a Friday.
    assert result.stdout.splitlines() == ["2024-02-01 06:30 Thu", "2024-03-01 06:30 Fri"]
