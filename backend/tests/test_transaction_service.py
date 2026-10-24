from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from backend.schemas import Transaction
from backend.services.transaction_service import normalize_transactions, parse_money


def test_normalized_values(frame):
    result = normalize_transactions(frame)
    item = result.transactions[0]
    assert item.id == 1
    assert item.date == date(2026, 10, 1)
    assert item.amount == Decimal("15000.50")
    assert item.balance == Decimal("-500.25")
    assert item.direction == "income"
    assert item.counterparty == "ООО Демо"
    assert item.description == "Оплата услуг"
    assert item.inn == "0000000001"
    assert (result.total_transactions, result.valid_transactions, result.invalid_transactions) == (1, 1, 0)


@pytest.mark.parametrize("value,expected", [
    ("15000", "15000"), ("15000.50", "15000.50"), ("15 000,50", "15000.50"),
    ("15\u00a0000,50", "15000.50"), ("15\u202f000,50", "15000.50"),
    ("999999999999999999.99", "999999999999999999.99"),
])
def test_money_precision(value, expected):
    assert parse_money(value) == Decimal(expected)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e5", "1,2,3", "1.000,50", "12 34", "100 руб", ""])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        parse_money(value)


@pytest.mark.parametrize("value", ["2026-10-01", "01.10.2026", "01/10/2026"])
def test_dates(frame, value):
    frame.loc[0, "date"] = value
    assert normalize_transactions(frame).transactions[0].date == date(2026, 10, 1)


@pytest.mark.parametrize("value,expected", [
    ("income", "income"), ("expense", "expense"), ("Списание", "expense"),
    (" РАСХОД ", "expense"), ("Кредит", "income"),
])
def test_directions(frame, value, expected):
    frame.loc[0, "direction"] = value
    assert normalize_transactions(frame).transactions[0].direction == expected


@pytest.mark.parametrize("field,value,error", [
    ("date", "2026-02-30", "дата"), ("date", None, "дата"),
    ("amount", "0", "больше нуля"), ("amount", "-10", "больше нуля"),
    ("amount", "abc", "число"), ("direction", "???", "направление"),
    ("direction", "", "направление"), ("counterparty", " ", "Контрагент"),
    ("inn", "123", "ИНН"),
    ("inn", "000000000a", "ИНН"), ("inn", "0000000001.0", "ИНН"),
    ("balance", "bad", "остаток"), ("balance", float("inf"), "остаток"),
])
def test_invalid_rows(frame, field, value, error):
    frame.loc[0, field] = value
    with pytest.raises(ValueError, match=error + ".*строке 2"):
        normalize_transactions(frame)


@pytest.mark.parametrize("value", [None, "", "   ", float("nan")])
def test_empty_description_becomes_unknown(frame, value):
    from backend.services.category_service import categorize_transaction
    from backend.services.recurring_service import detect_recurring_transactions
    frame.loc[0, "description"] = value
    result = normalize_transactions(frame)
    item = result.transactions[0]
    assert item.description == "unknown"
    assert item.amount == Decimal("15000.50")
    assert result.invalid_transactions == 0
    assert categorize_transaction(item).category == "unknown"
    repeated = [item, item.model_copy(update={"id": 2, "date": date(2026, 11, 1)}),
                item.model_copy(update={"id": 3, "date": date(2026, 12, 1)})]
    assert detect_recurring_transactions(repeated).groups == []


@pytest.mark.parametrize("field", ["date", "amount", "direction", "counterparty", "inn", "description"])
def test_missing_column(frame, field):
    with pytest.raises(ValueError, match="обязательный столбец"):
        normalize_transactions(frame.drop(columns=[field]))


@pytest.mark.parametrize("income,expense,expected", [("100,50", "", "income"), ("0", "10", "expense")])
def test_split_amounts(frame, income, expense, expected):
    frame = frame.drop(columns=["direction", "amount"])
    frame["Поступление"] = income
    frame["Списание"] = expense
    result = normalize_transactions(frame)
    assert result.transactions[0].direction == expected
    assert result.transactions[0].amount == Decimal("100.50" if expected == "income" else "10")


@pytest.mark.parametrize("income,expense", [("1", "2"), ("", ""), ("0", "0"), ("-1", "2")])
def test_invalid_split_amounts(frame, income, expense):
    frame = frame.drop(columns=["direction", "amount"])
    frame["income"], frame["expense"] = income, expense
    with pytest.raises(ValueError, match="ровно одна"):
        normalize_transactions(frame)


def test_present_direction_is_not_guessed(frame):
    frame["direction"] = ""
    frame["income"], frame["expense"] = "15000.50", "0"
    with pytest.raises(ValueError, match="направление"):
        normalize_transactions(frame)


def test_conflicting_split_amount(frame):
    frame = frame.drop(columns=["direction"])
    frame["income"], frame["expense"] = "10", "0"
    with pytest.raises(ValueError, match="не совпадает"):
        normalize_transactions(frame)


def test_duplicates_retained(row):
    result = normalize_transactions(pd.DataFrame([row, row, row]))
    assert result.duplicates == 2
    assert result.duplicate_groups[0].transaction_ids == [1, 2, 3]
    assert len(result.transactions) == 3


@pytest.mark.parametrize("changed", [
    {"counterparty": "Другой"}, {"date": "2026-10-02"},
    {"direction": "expense"}, {"description": "Другая оплата"},
])
def test_distinct_operations(row, changed):
    result = normalize_transactions(pd.DataFrame([row, {**row, **changed}]))
    assert result.duplicates == 0
    assert result.duplicate_groups == []


def test_dates_and_daily_balances_are_warnings(row):
    result = normalize_transactions(pd.DataFrame([
        {**row, "date": "2026-10-02"}, row, {**row, "balance": "999999"},
    ]))
    assert len(result.transactions) == 3
    assert any("не по возрастанию" in warning for warning in result.warnings)
    assert any("несколько операций" in warning for warning in result.warnings)


def test_schema_rejects_invalid_direction(row):
    with pytest.raises(ValidationError):
        Transaction(**{**row, "id": 1, "amount": "10", "balance": "0", "direction": "other"})
