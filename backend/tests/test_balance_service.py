from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backend.services.transaction_service import normalize_transactions
from backend.services.forecast_service import forecast_cash_flow
from backend.services.cash_gap_service import analyze_cash_gaps


def frame(rows, opening=None, kind="balance"):
    records = []
    for index, (day, amount, direction, balance) in enumerate(rows):
        record = {"date": day, "amount": amount, "direction": direction, "counterparty": "Демо",
                  "inn": "0000000001", "description": "Оплата аренды офиса"}
        if kind is not None:
            record[kind] = balance
        if opening is not None:
            record["Начальный остаток"] = opening
        records.append(record)
    return pd.DataFrame(records)


@pytest.mark.parametrize("direction,after,expected", [("income", "110000", "100000"), ("expense", "90000", "100000")])
def test_first_operation_sign(direction, after, expected):
    result = normalize_transactions(frame([("2026-01-01", "10000", direction, after)], kind="Баланс после операции"))
    assert result.balance_info.status == "detected"
    assert result.balance_info.kind == "after_transaction"
    assert result.balance_info.statement_opening_balance == Decimal(expected)
    assert result.balance_info.statement_closing_balance == Decimal(after)


def test_eod_uses_all_first_day_operations_and_sorts_dates():
    data = frame([("2026-01-02", "50", "expense", "1050"),
                  ("2026-01-01", "200", "income", "1100"),
                  ("2026-01-01", "100", "expense", "1100")])
    original = data.copy(deep=True)
    result = normalize_transactions(data)
    assert result.balance_info.status == "detected"
    assert result.balance_info.statement_opening_balance == 1000
    assert result.balance_info.statement_closing_balance == 1050
    assert result.transactions[0].date == date(2026, 1, 2)  # Исходный порядок не изменен.
    pd.testing.assert_frame_equal(data, original)


@pytest.mark.parametrize("kind", ["balance_after", "Баланс после операции"])
def test_same_day_after_balances_do_not_invent_order(kind):
    result = normalize_transactions(frame([("2026-01-01", "100", "income", "1100"),
                                           ("2026-01-01", "50", "expense", "1050")], kind=kind))
    assert result.balance_info.status == "unknown"
    assert result.balance_info.statement_opening_balance is None


def test_after_balances_check_whole_sequence_in_date_order():
    imported = normalize_transactions(frame([("2026-01-02", "50", "expense", "1050"),
                                              ("2026-01-01", "100", "income", "1100")], kind="balance_after_transaction"))
    assert imported.balance_info.status == "detected"
    assert imported.balance_info.statement_opening_balance == 1000
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.summary.opening_balance == 1050


def test_explicit_opening_without_balance_column():
    imported = normalize_transactions(frame([("2026-01-01", "100", "income", None),
                                              ("2026-01-02", "50", "expense", None)], opening="1000", kind=None))
    assert imported.balance_info.status == "provided"
    assert imported.balance_info.source == "statement_opening"
    assert imported.balance_info.statement_closing_balance == 1050
    assert all(item.balance is None for item in imported.transactions)
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.summary.opening_balance == 1050


def test_missing_balance_column_is_unknown_not_zero():
    imported = normalize_transactions(frame([("2026-01-01", "100", "expense", None)], kind=None))
    assert imported.balance_info.status == "unknown"
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.summary.opening_balance is None
    assert analyze_cash_gaps(forecast).status == "not_available"


@pytest.mark.parametrize("missing", [None, "", float("nan")])
def test_missing_balance_cell_is_unknown_not_zero(missing):
    imported = normalize_transactions(frame([("2026-01-01", "100", "expense", missing)]))
    assert imported.transactions[0].balance is None
    assert imported.balance_info.status == "unknown"


@pytest.mark.parametrize("opening", ["1000", "1000,00", "1 000.00"])
def test_explicit_opening_is_checked_and_not_replaced(opening):
    imported = normalize_transactions(frame([("2026-01-01", "100", "income", "1100")], opening=opening))
    assert imported.balance_info.status == "provided"
    assert imported.balance_info.statement_opening_balance == 1000


@pytest.mark.parametrize("mode", ["wrong_eod", "wrong_after", "wrong_explicit", "different_explicit", "malformed_explicit", "different_same_day"])
def test_inconsistent_history_preserves_reported_last_balance_without_modifying_transactions(mode):
    data = frame([("2026-01-01", "100", "income", "1100"), ("2026-01-02", "50", "expense", "1050")])
    if mode == "wrong_eod": data.loc[1, "balance"] = "950"
    elif mode == "wrong_after":
        data = data.rename(columns={"balance": "balance_after"})
        data.loc[1, "balance_after"] = "950"
    elif mode == "wrong_explicit": data["opening_balance"] = "999"
    elif mode == "different_explicit": data["opening_balance"] = ["1000", "900"]
    elif mode == "malformed_explicit": data["opening_balance"] = "bad"
    else:
        data.loc[1, "date"] = "2026-01-01"
    imported = normalize_transactions(data)
    assert imported.balance_info.status == "invalid"
    before = [item.model_dump() for item in imported.transactions]
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.balance_info.status == "invalid"
    if mode == "different_same_day":
        assert forecast.summary.opening_balance is None
        assert analyze_cash_gaps(forecast).has_cash_gap is None
    else:
        assert forecast.summary.opening_balance == Decimal("950" if mode in {"wrong_eod", "wrong_after"} else "1050")
        assert forecast.opening_balance_source == "statement_reported"
        assert analyze_cash_gaps(forecast).status != "not_available"
    assert [item.model_dump() for item in imported.transactions] == before


@pytest.mark.parametrize("initial,expected_closing,deficit", [("100000", "10000", "20000"), ("200000", "110000", "0"), ("90000", "0", "30000")])
def test_actual_forecast_uses_statement_end_once(initial, expected_closing, deficit):
    imported = normalize_transactions(frame([
        ("2025-12-01", "30000", "expense", None),
        ("2026-01-01", "30000", "expense", None),
        ("2026-02-01", "30000", "expense", None),
    ], opening=initial, kind=None))
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.as_of == date(2026, 2, 1)
    assert forecast.as_of_source == "statement_end"
    assert forecast.summary.opening_balance == Decimal(expected_closing)
    assert forecast.summary.total_expense == 30000
    assert forecast.summary.closing_balance == Decimal(expected_closing) - 30000
    analysis = analyze_cash_gaps(forecast)
    assert analysis.maximum_deficit == Decimal(deficit)
    if deficit != "0":
        assert analysis.first_gap_date == date(2026, 3, 1)


def test_explicit_later_date_does_not_move_old_balance_forward():
    imported = normalize_transactions(frame([("2026-01-01", "100", "income", "1100")]))
    forecast = forecast_cash_flow(imported.transactions, as_of=date(2026, 1, 2), balance_info=imported.balance_info)
    assert forecast.summary.opening_balance is None
    assert forecast.as_of_source == "explicit"
    assert forecast_cash_flow(imported.transactions, as_of=date(2026, 1, 2), opening_balance=Decimal(0), balance_info=imported.balance_info).summary.opening_balance == 0


def test_reimport_replaces_metadata_and_failed_import_preserves_it(client):
    data = frame([("2026-01-01", "100", "income", "1100"), ("2026-01-02", "50", "expense", "1050")])
    content = data.to_csv(index=False, sep=";").encode()
    first = client.post('/api/transactions/upload', files={'file': ('sample.csv', content)}).json()
    before = client.get('/api/forecast').json()
    again = client.post('/api/transactions/upload', files={'file': ('sample.csv', content)}).json()
    assert again == first
    assert client.get('/api/forecast').json() == before
    assert len(client.get('/api/transactions').json()) == 2
    assert client.post('/api/transactions/upload', files={'file': ('bad.csv', b'a;b\n1;2')}).status_code == 400
    assert client.get('/api/forecast').json() == before
    data.loc[1, 'balance'] = '999'
    client.post('/api/transactions/upload', files={'file': ('invalid.csv', data.to_csv(index=False, sep=';').encode())})
    assert Decimal(client.get('/api/forecast').json()['summary']['opening_balance']) == 999


@pytest.mark.parametrize("kind", ["balance", "balance_after"])
def test_reconciliation_reports_only_local_discrepancies(kind):
    imported = normalize_transactions(frame([
        ("2026-01-01", "100", "income", "1100"),
        ("2026-01-02", "100", "income", "1250"),
        ("2026-01-03", "100", "income", "1350"),
        ("2026-01-04", "100", "income", "1450"),
    ], kind=kind))
    assert imported.balance_info.status == "invalid"
    assert len(imported.balance_info.issues) == 1
    assert "2026-01-02" in imported.balance_info.issues[0]
    assert imported.balance_info.statement_closing_balance == 1450
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.summary.opening_balance == 1450


def test_one_cent_difference_does_not_block_last_day_balance():
    imported = normalize_transactions(frame([
        ("2026-01-01", "100", "income", "1100"),
        ("2026-01-02", "50", "expense", "1050.01"),
        ("2026-01-03", "50", "expense", "1000.01"),
    ]))
    assert len(imported.balance_info.issues) == 1
    assert "0.01" in imported.balance_info.issues[0]
    forecast = forecast_cash_flow(imported.transactions, balance_info=imported.balance_info)
    assert forecast.summary.opening_balance == Decimal("1000.01")
    assert forecast.warnings
