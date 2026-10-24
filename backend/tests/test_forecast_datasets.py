from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

import pytest

from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.forecast_service import forecast_cash_flow
from backend.services.recurring_service import detect_recurring_transactions
from backend.services.transaction_service import normalize_transactions


@pytest.mark.parametrize("filename,total,regular_count,expected", [
    ("C071.csv", 614, 6, ("8448890.98", "9133074.70", "-684183.72")),
    ("C015.csv", 2455, 3, ("1822130.74", "1833668.33", "-11537.59")),
], ids=["C071-forecast", "C015-forecast"])
@pytest.mark.parametrize("as_of", [date(2026, 10, 24), date(2026, 9, 30)], ids=["current-report-date", "last-statement-date"])
def test_full_dataset_forecast(request, client, filename, total, regular_count, expected, as_of):
    directory = request.config.getoption("--dataset-dir")
    if directory is None:
        pytest.skip("Для проверки оригиналов укажите --dataset-dir")
    path = Path(directory) / filename
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    imported = normalize_transactions(load_transactions_file(content, filename))
    categories = categorize_transactions(imported.transactions)
    category_map = {item.transaction_id: item for item in categories.categorizations}
    recurring = detect_recurring_transactions(imported.transactions, category_map)
    groups = {group.group_id: group for group in recurring.groups}
    before = [item.model_dump() for item in imported.transactions]
    result = forecast_cash_flow(imported.transactions, as_of=as_of, categorizations=category_map)
    assert result.history_transactions == total
    assert result.history_start == date(2026, 7, 3)
    assert result.history_end == date(2026, 9, 30)
    assert result.history_days == 90
    assert len(result.regular_group_ids) == regular_count
    assert len(result.daily) == 30
    assert result.start_date == as_of + timedelta(days=1)
    assert result.end_date == as_of + timedelta(days=30)
    assert len({(event.group_id, event.date) for event in result.scheduled_events}) == len(result.scheduled_events)
    by_id = {item.id: item for item in imported.transactions}
    regular_members = {item for group_id in result.regular_group_ids for item in groups[group_id].transaction_ids}
    residual_members = set(result.residual_history_transaction_ids)
    assert not regular_members & residual_members
    window_ids = {item.id for item in imported.transactions if item.date >= result.history_start}
    partition = result.regular_history_transaction_ids + result.residual_history_transaction_ids + result.excluded_history_transaction_ids
    assert set(partition) == window_ids
    assert len(partition) == len(window_ids)
    # Независимая арифметика: средние по указанным источникам + явное расписание.
    residual_totals = defaultdict(lambda: Decimal(0))
    for transaction_id in residual_members:
        item = by_id[transaction_id]
        residual_totals[(category_map[transaction_id].category, item.direction)] += item.amount
    expected_totals = {"income": Decimal(0), "expense": Decimal(0)}
    for (_, direction), amount in residual_totals.items():
        expected_totals[direction] += (amount / 3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    for event in result.scheduled_events:
        group = groups[event.group_id]
        assert group.status == "confirmed" and group.usable_for_calculations
        assert event.amount == group.average_amount
        assert result.start_date <= event.date <= result.end_date
        expected_totals[event.direction] += event.amount
    assert result.summary.total_income == expected_totals["income"]
    assert result.summary.total_expense == expected_totals["expense"]
    assert result.summary.net_cash_flow == expected_totals["income"] - expected_totals["expense"]
    assert sum(day.income for day in result.daily) == result.summary.total_income
    assert sum(day.expense for day in result.daily) == result.summary.total_expense
    assert sum(item.income for item in result.categories) == result.summary.total_income
    assert sum(item.expense for item in result.categories) == result.summary.total_expense
    cumulative = Decimal(0)
    for day in result.daily:
        assert day.net_cash_flow == day.income - day.expense
        cumulative += day.net_cash_flow
        assert day.cumulative_cash_flow == cumulative
        assert day.balance == (result.summary.opening_balance + cumulative if result.summary.opening_balance is not None else None)
    if as_of == date(2026, 10, 24):
        assert (result.summary.total_income, result.summary.total_expense, result.summary.net_cash_flow) == tuple(map(Decimal, expected))
        assert result.summary.opening_balance is result.summary.closing_balance is None
        assert result.history_lag_days == 24
    else:
        balances = {item.balance for item in imported.transactions if item.date == as_of}
        assert len(balances) == 1
        assert result.summary.opening_balance == next(iter(balances))
        assert result.summary.closing_balance == result.summary.opening_balance + result.summary.net_cash_flow
    assert [item.model_dump() for item in imported.transactions] == before
    assert forecast_cash_flow(list(reversed(imported.transactions)), as_of=as_of, categorizations=category_map) == result
    upload = client.post("/api/transactions/upload", files={"file": (filename, content)})
    assert upload.status_code == 200
    assert client.post("/api/transactions/categorize").status_code == 200
    recurring_before = client.get("/api/transactions/recurring").json()
    response = client.get("/api/forecast", params={"as_of": str(as_of)})
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert client.get("/api/forecast", params={"as_of": str(as_of)}).json() == response.json()
    assert client.get("/api/transactions").json() == upload.json()["transactions"]
    assert client.get("/api/transactions/recurring").json() == recurring_before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
