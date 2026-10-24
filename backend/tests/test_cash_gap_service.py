from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from backend.schemas import ForecastDay, ForecastEvent, ForecastSummary
from backend.services.cash_gap_service import analyze_cash_gaps
from backend.services.forecast_service import forecast_cash_flow


def make_forecast(balances, opening="100", start=date(2026, 1, 30)):
    with localcontext() as context:
        context.prec = 100
        base = forecast_cash_flow([], as_of=start - timedelta(days=1), opening_balance=Decimal(opening))
        previous, cumulative = Decimal(opening), Decimal(0)
        days = []
        for index, balance in enumerate(map(Decimal, balances)):
            net = balance - previous
            income, expense = max(net, Decimal(0)), max(-net, Decimal(0))
            cumulative += net
            days.append(ForecastDay(date=start + timedelta(days=index), income=income, expense=expense,
                                    net_cash_flow=net, cumulative_cash_flow=cumulative, balance=balance,
                                    regular_income=Decimal(0), regular_expense=Decimal(0),
                                    historical_income=income, historical_expense=expense))
            previous = balance
        return base.model_copy(update={
            "horizon_days": len(days), "start_date": start,
            "end_date": days[-1].date if days else start, "daily": days,
            "summary": ForecastSummary(total_income=sum((day.income for day in days), Decimal(0)),
                                       total_expense=sum((day.expense for day in days), Decimal(0)),
                                       net_cash_flow=cumulative, opening_balance=Decimal(opening), closing_balance=previous),
        })


@pytest.mark.parametrize("forecast", [None, make_forecast([])])
def test_no_forecast(forecast):
    result = analyze_cash_gaps(forecast)
    assert result.status == "not_available"
    assert result.has_cash_gap is None
    assert result.gap_count is result.maximum_deficit is None


@pytest.mark.parametrize("balances", [["100", "5", "0"], ["0", "0", "1"]])
def test_nonnegative_is_not_a_gap(balances):
    result = analyze_cash_gaps(make_forecast(balances))
    assert result.status == "no_gaps"
    assert result.has_cash_gap is False
    assert result.gap_count == result.ongoing_count == result.total_deficit_days == 0
    assert result.maximum_deficit == 0
    assert result.first_gap_date is None


def test_single_negative_day_and_recovery_at_zero():
    result = analyze_cash_gaps(make_forecast(["10", "-20", "0"]))
    assert result.status == "detected"
    assert result.has_cash_gap
    assert result.gap_count == 1
    gap = result.gaps[0]
    assert gap.start_date == date(2026, 1, 31)
    assert gap.last_negative_date == date(2026, 1, 31)
    assert gap.recovery_date == date(2026, 2, 1)
    assert gap.duration_days == 1
    assert gap.maximum_deficit == 20
    assert gap.status == "recovered"


def test_deficit_is_minimum_not_sum_of_negative_days():
    result = analyze_cash_gaps(make_forecast(["-10", "-50", "-20", "5"]))
    assert result.gap_count == 1
    gap = result.gaps[0]
    assert gap.minimum_balance == -50
    assert gap.maximum_deficit == 50  # Не 80.
    assert gap.minimum_balance_date == date(2026, 1, 31)
    assert gap.duration_days == result.total_deficit_days == 3
    assert gap.recovery_date == date(2026, 2, 2)


def test_separate_gaps_and_ongoing_tail():
    result = analyze_cash_gaps(make_forecast(["5", "-10", "0", "-20", "-5"]))
    assert result.gap_count == 2
    assert [gap.status for gap in result.gaps] == ["recovered", "ongoing"]
    assert [gap.duration_days for gap in result.gaps] == [1, 2]
    assert result.ongoing_count == 1
    assert result.gaps[-1].recovery_date is None
    assert result.gaps[-1].last_negative_date == date(2026, 2, 3)
    assert result.maximum_deficit == 20


@pytest.mark.parametrize("amount", ["-0.01", "-999999999999999999999999999999999999.99"])
def test_small_and_large_deficit_precision(amount):
    result = analyze_cash_gaps(make_forecast([amount, "0"]))
    assert result.maximum_deficit == Decimal(amount).copy_abs()


def test_initial_negative_balance_does_not_invent_onset_date():
    result = analyze_cash_gaps(make_forecast(["-5", "0", "-10"], opening="-100"))
    assert result.opening_balance_negative
    assert result.gaps[0].starts_before_forecast
    assert not result.gaps[1].starts_before_forecast
    assert result.gaps[0].start_date == result.start_date
    assert result.total_deficit_days == 2


def test_initial_negative_but_recovered_first_forecast_day():
    result = analyze_cash_gaps(make_forecast(["0", "1"], opening="-10"))
    assert result.status == "no_gaps"
    assert result.opening_balance_negative
    assert result.warnings


@pytest.mark.parametrize("missing", ["opening", "one_day", "all_days"])
def test_missing_balance_is_unknown_not_no_gaps(missing):
    forecast = make_forecast(["10", "-20", "0"])
    if missing == "opening":
        forecast = forecast.model_copy(update={"summary": forecast.summary.model_copy(update={"opening_balance": None})})
    else:
        forecast = forecast.model_copy(update={"daily": [day.model_copy(update={"balance": None}) if missing == "all_days" or index == 1 else day
                                                         for index, day in enumerate(forecast.daily)]})
    result = analyze_cash_gaps(forecast)
    assert result.status == "not_available"
    assert result.has_cash_gap is None
    assert result.maximum_deficit is None
    assert result.daily  # Доступно изменение потока, но не утверждение о дефиците.


def test_input_order_and_immutability():
    forecast = make_forecast(["5", "-10", "-50", "0"])
    expected = analyze_cash_gaps(forecast)
    shuffled = forecast.model_copy(update={"daily": list(reversed(forecast.daily))})
    before = shuffled.model_dump()
    assert analyze_cash_gaps(shuffled) == expected
    assert shuffled.model_dump() == before


@pytest.mark.parametrize("mode", ["duplicate", "missing", "bad_date", "bad_amount", "bad_net", "bad_summary", "negative_income", "missing_amount", "bad_horizon"])
def test_malformed_forecast_returns_not_available(mode):
    forecast = make_forecast(["10", "-20", "0"])
    days = list(forecast.daily)
    if mode == "duplicate":
        days[1] = days[1].model_copy(update={"date": days[0].date})
    elif mode == "missing":
        days.pop(1)
    elif mode == "bad_date":
        days[1] = days[1].model_copy(update={"date": "impossible"})
    elif mode == "bad_amount":
        days[1] = days[1].model_copy(update={"balance": Decimal("NaN")})
    elif mode == "bad_net":
        days[1] = days[1].model_copy(update={"net_cash_flow": Decimal("1000")})
    elif mode == "negative_income":
        days[1] = days[1].model_copy(update={"income": Decimal("-5")})
    elif mode == "missing_amount":
        days[1] = days[1].model_copy(update={"expense": None})
    elif mode == "bad_horizon":
        forecast = forecast.model_copy(update={"horizon_days": None})
    else:
        forecast = forecast.model_copy(update={"summary": forecast.summary.model_copy(update={"closing_balance": Decimal("200")})})
    result = analyze_cash_gaps(forecast.model_copy(update={"daily": days}))
    assert result.status == "not_available"
    assert result.has_cash_gap is None
    assert result.warnings


def test_short_declared_horizon_only_analyzes_known_days():
    result = analyze_cash_gaps(make_forecast(["-10", "-20"]))
    assert result.analyzed_days == result.total_deficit_days == 2
    assert result.gaps[0].status == "ongoing"
    assert any("короткий" in warning for warning in result.warnings)


def test_related_events_are_evidence_not_a_new_forecast():
    forecast = make_forecast(["90", "-50", "-20", "10"])
    events = [
        ForecastEvent(group_id="rent", date=forecast.daily[1].date, category="rent", direction="expense", amount="140"),
        ForecastEvent(group_id="income", date=forecast.daily[3].date, category="sales", direction="income", amount="30"),
    ]
    forecast = forecast.model_copy(update={"scheduled_events": events})
    gap = analyze_cash_gaps(forecast).gaps[0]
    assert gap.start_day.expense == 140
    assert gap.recovery_day.income == 30
    assert gap.related_events == events
    assert gap.regular_expense_categories == ["rent"]
    assert gap.largest_expense_day.date == gap.start_date
    assert gap.preceding_days == [forecast.daily[0]]


def test_no_specific_events_means_daily_aggregates_only():
    gap = analyze_cash_gaps(make_forecast(["5", "-10", "0"])).gaps[0]
    assert gap.related_events == gap.regular_expense_categories == []
    assert gap.start_day.expense == 15
    assert gap.recovery_day.income == 10


def test_largest_preceding_expense_is_context_not_an_asserted_cause():
    forecast = make_forecast(["100", "50", "-10", "0"], opening="1000")
    gap = analyze_cash_gaps(forecast).gaps[0]
    assert gap.largest_expense_day.date == forecast.daily[0].date
    assert gap.largest_expense_day.expense == 900
    assert gap.start_day.expense == 60
    assert len(gap.preceding_days) == 2


def test_analysis_does_not_call_forecast_builder(monkeypatch):
    from backend.services import forecast_service
    forecast = make_forecast(["10", "-10", "0"])
    def forbidden(*args, **kwargs):
        pytest.fail("Анализ готового прогноза не должен заново строить прогноз")
    monkeypatch.setattr(forecast_service, "forecast_cash_flow", forbidden)
    assert analyze_cash_gaps(forecast).gap_count == 1


def test_invalid_date_model_boundary():
    with pytest.raises(ValidationError):
        ForecastEvent(group_id="demo", date="2026-02-30", category="unknown", direction="income", amount="1")
