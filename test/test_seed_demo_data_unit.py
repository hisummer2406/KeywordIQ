from datetime import date

from scripts import seed_demo_data


def test_weekly_periods_builds_seven_consecutive_report_weeks():
    assert hasattr(seed_demo_data, "weekly_periods")
    assert seed_demo_data.weekly_periods(date(2026, 7, 12), 7) == [
        date(2026, 5, 31),
        date(2026, 6, 7),
        date(2026, 6, 14),
        date(2026, 6, 21),
        date(2026, 6, 28),
        date(2026, 7, 5),
        date(2026, 7, 12),
    ]
