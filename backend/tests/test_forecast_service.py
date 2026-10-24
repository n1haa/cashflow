from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas import Categorization, Transaction
from backend.services.forecast_service import forecast_cash_flow


def transactions(dates, *, direction="expense", amounts=None, irregular=False):
    return [Transaction(id=index + 1, date=day, amount=(amounts[index] if amounts else "100.00"),
                        direction=direction, counterparty="Демо", inn=None,
                        description=f"Разовая операция {index}" if irregular else "Оплата аренды офиса",
                        balance="1000.00") for index, day in enumerate(dates)]


def assert_consistent(result):
    assert len(result.daily) == 30
    assert [item.date for item in result.daily] == [result.as_of + timedelta(days=index) for index in range(1, 31)]
    assert result.summary.total_income == sum(item.income for item in result.daily)
    assert result.summary.total_expense == sum(item.expense for item in result.daily)
    assert result.summary.net_cash_flow == result.summary.total_income - result.summary.total_expense
    assert result.summary.total_income == sum(item.income for item in result.categories)
    assert result.summary.total_expense == sum(item.expense for item in result.categories)
    cumulative = Decimal(0)
    for item in result.daily:
        assert item.income == item.regular_income + item.historical_income
        assert item.expense == item.regular_expense + item.historical_expense
        assert item.net_cash_flow == item.income - item.expense
        cumulative += item.net_cash_flow
        assert item.cumulative_cash_flow == cumulative
        if result.summary.opening_balance is None:
            assert item.balance is None
        else:
            assert item.balance == result.summary.opening_balance + cumulative
    assert result.summary.closing_balance == result.daily[-1].balance
    partitions = [result.regular_history_transaction_ids, result.residual_history_transaction_ids, result.excluded_history_transaction_ids]
    assert sum(map(len, partitions)) == result.history_window_transactions
    assert len(set(sum(partitions, []))) == result.history_window_transactions


def test_empty_and_unknown_balance():
    result = forecast_cash_flow([], as_of=date(2026, 1, 1))
    assert result.summary.opening_balance is result.summary.closing_balance is None
    assert result.summary.total_income == result.summary.total_expense == 0
    assert result.history_transactions == 0
    assert result.warnings
    assert_consistent(result)


def test_one_transaction_does_not_create_fake_income():
    result = forecast_cash_flow(transactions(["2026-01-01"]), as_of=date(2026, 1, 1))
    assert result.summary.opening_balance == 1000
    assert result.summary.total_income == result.summary.total_expense == 0
    assert result.excluded_history_transaction_ids == [1]
    assert_consistent(result)


@pytest.mark.parametrize("direction", ["income", "expense"])
def test_weekly_series_projects_all_occurrences(direction):
    result = forecast_cash_flow(transactions(["2026-01-05", "2026-01-12", "2026-01-19"], direction=direction), as_of=date(2026, 1, 19))
    assert [event.date for event in result.scheduled_events] == [date(2026, 1, 26), date(2026, 2, 2), date(2026, 2, 9), date(2026, 2, 16)]
    assert result.summary.total_income == (400 if direction == "income" else 0)
    assert result.summary.total_expense == (400 if direction == "expense" else 0)
    assert result.residual_history_transaction_ids == []
    assert_consistent(result)


@pytest.mark.parametrize("dates,as_of,expected", [
    (["2025-11-30", "2025-12-31", "2026-01-31"], "2026-01-31", ["2026-02-28"]),
    (["2023-11-30", "2023-12-31", "2024-01-31"], "2024-01-31", ["2024-02-29"]),
    (["2025-11-30", "2025-12-30", "2026-01-30"], "2026-01-31", ["2026-02-28", "2026-03-30"]),
    (["2025-11-30", "2025-12-30", "2026-01-30"], "2026-02-28", ["2026-03-30"]),
    (["2025-11-01", "2025-12-01", "2026-01-01"], "2026-01-31", ["2026-02-01", "2026-03-01"]),
    (["2025-01-31", "2025-04-30", "2025-07-31"], "2025-10-01", ["2025-10-31"]),
    (["2023-02-28", "2024-02-29", "2025-02-28"], "2026-02-01", ["2026-02-28"]),
])
def test_calendar_schedule(dates, as_of, expected):
    calculation_date = date.fromisoformat(as_of)
    result = forecast_cash_flow(transactions(dates), as_of=calculation_date)
    expected = [date.fromisoformat(day) for day in expected if date.fromisoformat(day) <= calculation_date + timedelta(days=30)]
    assert [event.date for event in result.scheduled_events] == expected
    assert_consistent(result)


@pytest.mark.parametrize("as_of,boundary", [(date(2026, 1, 22), "start"), (date(2026, 1, 21), "end")])
def test_schedule_includes_first_and_last_day(as_of, boundary):
    result = forecast_cash_flow(transactions(["2026-01-02", "2026-01-09", "2026-01-16"]), as_of=as_of)
    if boundary == "start":
        assert result.scheduled_events[0].date == result.start_date
    else:
        assert result.scheduled_events[-1].date == result.end_date
    assert len(result.scheduled_events) == 5
    assert_consistent(result)


def test_outside_horizon_still_excluded_from_mean():
    result = forecast_cash_flow(transactions(["2025-11-05", "2025-12-05", "2026-01-05"]), as_of=date(2026, 1, 5))
    assert len(result.regular_group_ids) == 1
    assert result.scheduled_events == []
    assert result.summary.total_expense == 0
    assert result.residual_history_transaction_ids == []


def test_two_operations_and_unknown_period_not_scheduled():
    for days in (["2026-01-01", "2026-02-01"], ["2026-01-01", "2026-01-03", "2026-01-11"]):
        result = forecast_cash_flow(transactions(days), as_of=date.fromisoformat(days[-1]))
        assert result.regular_group_ids == []
        assert result.scheduled_events == []
        assert result.excluded_regular_groups


def test_variable_confirmed_series_uses_only_mean_when_supported():
    items = transactions(["2025-11-01", "2025-12-01", "2026-01-01"], amounts=["100", "200", "300"])
    result = forecast_cash_flow(items, as_of=date(2026, 1, 1))
    assert result.regular_group_ids == []
    assert result.residual_history_transaction_ids == [1, 2, 3]
    assert result.scheduled_events == []
    assert_consistent(result)


@pytest.mark.parametrize("direction", ["income", "expense"])
def test_residual_mean_and_unknown_category(direction):
    items = transactions(["2026-01-01", "2026-01-15", "2026-01-30"], amounts=["100", "200", "300"], direction=direction, irregular=True)
    result = forecast_cash_flow(items, as_of=date(2026, 1, 30))
    assert result.history_days == 30
    assert result.regular_group_ids == []
    assert result.categories[0].category == "unknown"
    assert result.summary.total_income == (600 if direction == "income" else 0)
    assert result.summary.total_expense == (600 if direction == "expense" else 0)
    assert all(getattr(item, direction) == 20 for item in result.daily)
    assert_consistent(result)


def test_fractional_cents_do_not_accumulate_rounding_error():
    items = transactions(["2026-01-01", "2026-01-15", "2026-01-30"], amounts=["0.33", "0.33", "0.34"], irregular=True)
    result = forecast_cash_flow(items, as_of=date(2026, 1, 30))
    assert result.summary.total_expense == Decimal("1.00")
    assert set(item.expense for item in result.daily) == {Decimal("0.03"), Decimal("0.04")}
    assert_consistent(result)


def test_balance_is_not_inferred_across_history_gap():
    items = transactions(["2026-01-01"])
    result = forecast_cash_flow(items, as_of=date(2026, 1, 2))
    assert result.summary.opening_balance is None
    assert result.history_lag_days == 1
    assert_consistent(result)
    result = forecast_cash_flow(items, as_of=date(2026, 1, 2), opening_balance=Decimal("-50.25"))
    assert result.summary.opening_balance == Decimal("-50.25")
    assert result.opening_balance_source == "provided"
    assert_consistent(result)


def test_conflicting_daily_balances_not_guessed():
    items = transactions(["2026-01-01", "2026-01-01"])
    items[1] = items[1].model_copy(update={"balance": Decimal("999")})
    result = forecast_cash_flow(items, as_of=date(2026, 1, 1))
    assert result.summary.opening_balance is None


def test_multiple_events_on_same_day_and_category_totals():
    items = transactions(["2025-11-01", "2025-12-01", "2026-01-01"])
    other = [item.model_copy(update={"id": item.id + 3, "counterparty": "Другой", "direction": "income"}) for item in items]
    result = forecast_cash_flow(items + other, as_of=date(2026, 1, 31))
    assert len(result.regular_group_ids) == 2
    assert len(result.scheduled_events) == 4
    assert result.daily[0].regular_income == result.daily[0].regular_expense == 100
    assert_consistent(result)


def test_future_rows_do_not_leak_into_analysis_or_balance():
    items = transactions(["2026-01-01", "2026-02-01", "2026-03-01"])
    result = forecast_cash_flow(items, as_of=date(2026, 1, 1))
    assert result.history_transactions == 1
    assert result.scheduled_events == []
    assert result.summary.total_expense == 0


def test_stale_history_not_extrapolated_forever():
    result = forecast_cash_flow(transactions(["2025-01-01", "2025-02-01", "2025-03-01"]), as_of=date(2026, 1, 1))
    assert result.regular_group_ids == []
    assert result.residual_history_transaction_ids == []
    assert result.summary.total_expense == 0


def test_default_date_and_determinism(monkeypatch):
    from backend.services import forecast_service
    monkeypatch.setattr(forecast_service, "project_date", lambda: date(2026, 1, 1))
    items = transactions(["2025-11-01", "2025-12-01", "2026-01-01"])
    before = [item.model_dump() for item in items]
    result = forecast_cash_flow(items)
    assert result.as_of == date(2026, 1, 1)
    assert result == forecast_cash_flow(list(reversed(items)))
    assert [item.model_dump() for item in items] == before


def test_errors_are_explicit():
    with pytest.raises(ValueError, match="30 календарных дней"):
        forecast_cash_flow([], as_of=date.max)
    with pytest.raises(ValueError, match="конечным"):
        forecast_cash_flow([], opening_balance=Decimal("NaN"))
    item = transactions(["2026-01-01"])[0]
    with pytest.raises(ValueError, match="уникальными"):
        forecast_cash_flow([item, item])
    with pytest.raises(ValidationError):
        Transaction.model_validate({**item.model_dump(), "amount": "NaN"})


def test_regular_and_residual_same_category_without_double_counting():
    regular = transactions(["2025-11-01", "2025-12-01", "2026-01-01"])
    residual = [item.model_copy(update={"id": item.id + 3, "counterparty": "Другая аренда", "description": f"Аренда объекта {index}"})
                for index, item in enumerate(transactions(["2025-11-03", "2025-12-11", "2026-01-01"]))]
    result = forecast_cash_flow(regular + residual, as_of=date(2026, 1, 31))
    assert result.regular_history_transaction_ids == [1, 2, 3]
    assert result.residual_history_transaction_ids == [4, 5, 6]
    assert len(result.scheduled_events) == 2
    assert result.summary.total_expense == Decimal("345.16")  # 2 * 100 + round(300 * 30 / 62).
    assert_consistent(result)


def test_biweekly_unknown_category_and_large_amounts():
    items = transactions(["2026-01-01", "2026-01-15", "2026-01-29"], amounts=["999999999999999999.99"] * 3)
    categories = {item.id: Categorization(transaction_id=item.id, source="test", reason="нет категории") for item in items}
    result = forecast_cash_flow(items, as_of=date(2026, 1, 29), categorizations=categories)
    assert [event.date for event in result.scheduled_events] == [date(2026, 2, 12), date(2026, 2, 26)]
    assert result.categories[0].category == "unknown"
    assert result.summary.total_expense == Decimal("1999999999999999999.98")
    assert_consistent(result)
