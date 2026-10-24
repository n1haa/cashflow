from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest
from pydantic import ValidationError

from backend.schemas import Categorization, Transaction
from backend.services.recurring_service import detect_recurring_transactions, normalize_recurring_description


def make_transactions(dates, amounts=None, **updates):
    return [Transaction(
        **{"id": index + 1, "date": day, "amount": (amounts[index] if amounts else "100.00"),
           "direction": "expense", "counterparty": "ООО Демо", "inn": "0000000001",
           "description": "Оплата аренды офиса по договору № 12/24", "balance": "1000.00", **updates},
    ) for index, day in enumerate(dates)]


@pytest.mark.parametrize("dates,frequency,next_date", [
    (["2026-01-05", "2026-01-12", "2026-01-19"], "weekly", "2026-01-26"),
    (["2026-01-05", "2026-01-19", "2026-02-02"], "biweekly", "2026-02-16"),
    (["2026-01-05", "2026-02-05", "2026-03-05"], "monthly", "2026-04-05"),
    (["2026-01-05", "2026-04-05", "2026-07-05"], "quarterly", "2026-10-05"),
    (["2024-06-01", "2025-06-01", "2026-06-01"], "yearly", "2027-06-01"),
    (["2026-01-31", "2026-02-28", "2026-03-31"], "monthly", "2026-04-30"),
    (["2024-01-31", "2024-02-29", "2024-03-31"], "monthly", "2024-04-30"),
    (["2026-01-30", "2026-02-28", "2026-03-30"], "monthly", "2026-04-30"),
    (["2026-01-03", "2026-02-08", "2026-03-04"], "monthly", "2026-04-04"),
    (["2024-02-29", "2025-02-28", "2026-02-28"], "yearly", "2027-02-28"),
    (["2025-07-31", "2025-10-31", "2026-01-31"], "quarterly", "2026-04-30"),
])
def test_periods_and_calendar(dates, frequency, next_date):
    result = detect_recurring_transactions(make_transactions(dates))
    assert result.total_groups == result.confirmed_groups == 1
    group = result.groups[0]
    assert group.frequency == frequency
    assert str(group.next_expected_date) == next_date
    assert group.status == "confirmed"
    assert group.usable_for_calculations
    assert not group.date_variable
    assert group.repetitions == group.distinct_dates == 3


def test_empty_and_single():
    assert detect_recurring_transactions([]).total_groups == 0
    result = detect_recurring_transactions(make_transactions(["2026-01-01"]))
    assert result.groups == []
    assert result.ungrouped_transaction_ids == [1]


def test_two_are_only_potential():
    group = detect_recurring_transactions(make_transactions(["2026-01-05", "2026-02-05"])).groups[0]
    assert group.frequency == "monthly"
    assert group.status == "potential"
    assert group.next_expected_date == date(2026, 3, 5)
    assert not group.usable_for_calculations


@pytest.mark.parametrize("dates", [
    ["2026-01-01", "2026-01-03", "2026-04-15"],
    ["2026-01-01", "2026-02-01", "2026-04-01"],
    ["2026-01-01", "2026-01-01", "2026-01-01"],
    [str(date(2026, 1, 1) + timedelta(days=index)) for index in range(30)],
    [str(date(2026, 1, 1) + timedelta(days=index * 9)) for index in range(20)],
])
def test_irregular_daily_or_drifting_series(dates):
    group = detect_recurring_transactions(make_transactions(dates)).groups[0]
    assert group.frequency == "unknown"
    assert group.status == "uncertain"
    assert group.next_expected_date is None
    assert not group.usable_for_calculations


def test_rare_delays_do_not_hide_whole_series():
    dates = [date(2025, month, 1) for month in range(1, 13)]
    dates[0] = date(2025, 1, 9)
    group = detect_recurring_transactions(make_transactions(dates)).groups[0]
    assert group.status == "confirmed"
    assert group.frequency == "monthly"
    assert group.date_exceptions == 1
    assert group.interval_exceptions == 0
    assert not group.date_variable


def test_monthly_payments_with_variable_days_are_confirmed():
    dates = [date(2025, month, 1 if month % 2 else 15) for month in range(1, 13)]
    group = detect_recurring_transactions(make_transactions(dates)).groups[0]
    assert group.status == "confirmed"
    assert group.frequency == "monthly"
    assert group.date_variable
    assert group.next_expected_date == date(2026, 1, 1)
    assert not group.usable_for_calculations


@pytest.mark.parametrize("dates,status,next_date", [
    (["2025-12-02", "2026-01-28"], "potential", "2026-02-02"),
    (["2025-12-31", "2026-01-02", "2026-02-28"], "confirmed", "2026-03-28"),
    (["2024-01-01", "2024-02-29", "2024-03-31"], "confirmed", "2024-04-29"),
    (["9999-10-01", "9999-11-28", "9999-12-02"], "confirmed", None),
])
def test_variable_monthly_days_calendar_boundaries(dates, status, next_date):
    items = make_transactions(dates)
    result = detect_recurring_transactions(items)
    group = result.groups[0]
    assert group.status == status
    assert group.frequency == "monthly"
    assert group.date_variable
    assert group.next_expected_date == (date.fromisoformat(next_date) if next_date else None)
    assert not group.usable_for_calculations
    assert detect_recurring_transactions(list(reversed(items))) == result


@pytest.mark.parametrize("dates", [
    ["2025-12-02", "2026-01-28", "2026-03-03"],  # Пропущен месяц.
    ["2026-01-01", "2026-02-15", "2026-02-20", "2026-03-01"],  # Два платежа в месяце.
])
def test_variable_days_do_not_hide_missing_or_duplicate_months(dates):
    group = detect_recurring_transactions(make_transactions(dates)).groups[0]
    assert group.status == "uncertain"
    assert group.frequency == "unknown"


def test_variable_monthly_days_with_duplicate_date_are_only_potential():
    items = make_transactions(["2026-01-01", "2026-02-28", "2026-02-28", "2026-03-02"])
    group = detect_recurring_transactions(items).groups[0]
    assert group.frequency == "monthly"
    assert group.status == "potential"
    assert group.repetitions == 4
    assert group.distinct_dates == 3
    assert not group.usable_for_calculations


def test_same_day_payments_preserved_as_potential():
    dates = [date(2026, 1, 5) + timedelta(days=7 * index) for index in range(10)]
    dates.insert(2, dates[2])
    group = detect_recurring_transactions(make_transactions(dates)).groups[0]
    assert group.frequency == "weekly"
    assert group.status == "potential"
    assert group.distinct_dates == 10
    assert group.repetitions == len(group.transaction_ids) == 11
    assert not group.usable_for_calculations


def test_early_month_payment_in_previous_month():
    group = detect_recurring_transactions(make_transactions([
        "2025-11-05", "2025-12-05", "2025-12-28", "2026-02-05",
        "2026-03-05", "2026-04-05", "2026-05-05", "2026-06-05",
    ])).groups[0]
    assert group.frequency == "monthly"
    assert group.status == "confirmed"
    assert group.date_exceptions == 1
    assert group.next_expected_date == date(2026, 7, 5)


@pytest.mark.parametrize("direction", ["income", "expense"])
@pytest.mark.parametrize("amounts,variable,expected", [
    (["100.00", "100.00", "100.00"], False, "100.00"),
    (["100.00", "110.01", "90.00"], False, "100.00"),
    (["100.00", "1000.00", "200.00"], True, "433.33"),
    (["999999999999999999.99"] * 3, False, "999999999999999999.99"),
])
def test_money_exact_and_direction(direction, amounts, variable, expected):
    items = make_transactions(["2026-01-01", "2026-02-01", "2026-03-01"], amounts, direction=direction)
    before = [item.model_dump() for item in items]
    group = detect_recurring_transactions(items).groups[0]
    assert group.average_amount == Decimal(expected)
    assert group.minimum_amount == min(map(Decimal, amounts))
    assert group.maximum_amount == max(map(Decimal, amounts))
    assert group.amount_variable == variable
    assert group.usable_for_calculations == (not variable)
    assert group.direction == direction
    assert [item.model_dump() for item in items] == before


@pytest.mark.parametrize("amount", ["-10", "0"])
def test_nonpositive_amounts_still_rejected(amount):
    with pytest.raises(ValidationError):
        make_transactions(["2026-01-01"], [amount])


@pytest.mark.parametrize("field,value", [
    ("counterparty", "Другой контрагент"), ("inn", "0000000002"),
    ("description", "Аренда склада по договору №99"), ("direction", "income"),
])
def test_distinct_operations_not_merged(field, value):
    items = make_transactions(["2026-01-01", "2026-02-01", "2026-03-01"])
    items[1] = items[1].model_copy(update={field: value})
    result = detect_recurring_transactions(items)
    assert result.confirmed_groups == 0
    assert result.ungrouped_transaction_ids == [2]


def test_categories_are_additional_and_unknown_is_supported():
    items = make_transactions(["2026-01-01", "2026-02-01", "2026-03-01"])
    categories = {item.id: Categorization(transaction_id=item.id, source="test", reason="test") for item in items}
    result = detect_recurring_transactions(items, categories)
    assert result.groups[0].category == "unknown"
    assert result.groups[0].status == "confirmed"
    categories[2] = categories[2].model_copy(update={"category": "software"})
    result = detect_recurring_transactions(items, categories)
    assert result.confirmed_groups == 0
    assert result.ungrouped_transaction_ids == [2]


def test_dynamic_text_removed_but_contract_and_object_preserved():
    first = "Оплата аренды офиса №1 по договору №12/24 за январь 2026 г. Сумма 100.00 В т.ч. НДС 20%"
    second = " ОПЛАТА  аренды офиса №1 по договору №12/24 за февраль 2026 г. Без НДС"
    assert normalize_recurring_description(first) == normalize_recurring_description(second)
    assert normalize_recurring_description(first) != normalize_recurring_description(second.replace("офиса №1", "офиса №2"))
    assert normalize_recurring_description(first) != normalize_recurring_description(second.replace("12/24", "13/24"))
    assert normalize_recurring_description("Зарплата (аванс)") != normalize_recurring_description("Зарплата (окончательный расчет)")


def test_invoice_numbers_and_dates_do_not_split_group():
    a = "Оплата по счету №123 от 01.01.2026 за услуги уборки. НДС не облагается"
    b = "Оплата по счету №456 от 02.02.2026 за услуги уборки. Сумма 100.00"
    assert normalize_recurring_description(a) == normalize_recurring_description(b)


def test_vague_purpose_is_not_evidence():
    items = make_transactions(["2026-01-01", "2026-02-01", "2026-03-01"], description="Платеж")
    result = detect_recurring_transactions(items)
    assert result.groups == []
    assert result.ungrouped_transaction_ids == [1, 2, 3]


def test_deterministic_and_duplicate_ids_rejected():
    items = make_transactions(["2026-01-01", "2026-02-01", "2026-03-01"])
    assert detect_recurring_transactions(items) == detect_recurring_transactions(list(reversed(items)))
    with pytest.raises(ValueError, match="уникальными"):
        detect_recurring_transactions([items[0], items[0]])


def test_calendar_overflow_is_explicit():
    group = detect_recurring_transactions(make_transactions(["9999-10-31", "9999-11-30", "9999-12-31"])).groups[0]
    assert group.frequency == "monthly"
    assert group.next_expected_date is None
    assert not group.usable_for_calculations


def test_large_gap_cannot_be_hidden_as_single_exception():
    dates = [date(2020, 1, 6) + timedelta(days=index * 7) for index in range(10)]
    dates.append(date(2026, 1, 5))
    assert detect_recurring_transactions(make_transactions(dates)).groups[0].frequency == "unknown"
