from datetime import date

from app.table.analysis.analysis_crud import shape_trend_dates, shape_trends
from app.table.search.search_schemas import DayRowItem


def test_trend_values_and_dates_keep_the_same_order():
    rows = [
        (7, date(2026, 7, 17), 20),
        (7, date(2026, 7, 18), 10),
    ]

    assert shape_trends(rows) == {7: [20, 10]}
    assert shape_trend_dates(rows) == {7: [date(2026, 7, 17), date(2026, 7, 18)]}


def test_day_row_serializes_trend_dates():
    item = DayRowItem(
        keyword_id=7,
        keyword="coffee maker",
        current_ranking=10,
        previous_ranking=20,
        ranking_change=-10,
        is_new=False,
        trend=[20, 10],
        trend_dates=[date(2026, 7, 17), date(2026, 7, 18)],
    )

    assert item.model_dump(mode="json")["trend_dates"] == ["2026-07-17", "2026-07-18"]
