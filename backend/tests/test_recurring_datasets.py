"""Проверка всех исходных операций Sprint 1–2 и групп Sprint 3 на двух оригиналах."""

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

import pytest

from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.recurring_service import detect_recurring_transactions
from backend.services.transaction_service import normalize_transactions


@pytest.mark.parametrize("filename,total,counts,frequencies", [
    ("C071.csv", 614, (50, 6, 4, 40), {"monthly": 7, "quarterly": 2, "unknown": 40, "weekly": 1}),
    ("C015.csv", 2455, (25, 8, 4, 13), {"monthly": 7, "unknown": 13, "weekly": 5}),
], ids=["C071-recurrence", "C015-recurrence"])
def test_full_dataset_recurrence(request, client, filename, total, counts, frequencies):
    directory = request.config.getoption("--dataset-dir")
    if directory is None:
        pytest.skip("Для проверки оригиналов укажите --dataset-dir")
    path = Path(directory) / filename
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    imported = normalize_transactions(load_transactions_file(content, filename))
    categories = categorize_transactions(imported.transactions)
    category_map = {item.transaction_id: item for item in categories.categorizations}
    before = [item.model_dump() for item in imported.transactions]
    result = detect_recurring_transactions(imported.transactions, category_map)
    assert result.total_transactions == total
    assert (result.total_groups, result.confirmed_groups, result.potential_groups, result.uncertain_groups) == counts
    assert result.frequency_distribution == frequencies
    # Счетчики — регрессия правил, не эталон экспертной разметки.
    all_ids = result.ungrouped_transaction_ids + [item for group in result.groups for item in group.transaction_ids]
    assert sorted(all_ids) == list(range(1, total + 1))
    assert len(all_ids) == len(set(all_ids))
    by_id = {item.id: item for item in imported.transactions}
    for group in result.groups:
        items = [by_id[item] for item in group.transaction_ids]
        assert group.repetitions == len(items)
        assert group.distinct_dates == len({item.date for item in items})
        assert len({(item.counterparty, item.inn, item.direction) for item in items}) == 1
        assert {category_map[item.id].category for item in items} == {group.category}
        assert group.first_date == min(item.date for item in items)
        assert group.last_date == max(item.date for item in items)
        assert group.average_amount == (sum(item.amount for item in items) / len(items)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert group.minimum_amount == min(item.amount for item in items)
        assert group.maximum_amount == max(item.amount for item in items)
        if group.frequency == "unknown":
            assert group.status == "uncertain"
            assert group.next_expected_date is None
        else:
            assert group.next_expected_date > group.last_date
            assert group.date_exceptions * 100 <= group.distinct_dates * 15
            assert group.interval_exceptions * 100 <= (group.distinct_dates - 1) * 15
        if group.status == "confirmed":
            assert group.distinct_dates == group.repetitions >= 3
        if group.status == "potential":
            assert not group.usable_for_calculations

    rent = next(group for group in result.groups if group.category == "rent")
    assert rent.frequency == "monthly"
    assert rent.status == "confirmed"
    assert str(rent.next_expected_date) == "2026-10-04"
    if filename == "C071.csv":
        fee = next(group for group in result.groups if group.category == "bank_fees")
        assert fee.average_amount == Decimal("1990.00")
        assert str(fee.next_expected_date) == "2026-10-01"
        payroll = [group for group in result.groups if group.category == "payroll"]
        assert len(payroll) == 2  # Аванс и окончательный расчет не объединены.
    else:
        equipment = next(group for group in result.groups if group.category == "equipment_rent")
        assert equipment.average_amount == Decimal("3600.00")
        assert str(equipment.next_expected_date) == "2026-10-03"
        contractors = [group for group in result.groups if group.category == "contractors"]
        assert len(contractors) == 4  # Разные люди не объединены.
        assert all(group.frequency == "weekly" and group.status == "potential" for group in contractors)
        assert all(group.repetitions == 104 and group.distinct_dates == 102 for group in contractors)
        acquiring = [group for group in result.groups if group.repetitions == 730]
        assert len(acquiring) == 2
        assert all(group.frequency == "unknown" for group in acquiring)  # Ежедневный тип не поддерживается.

    assert [item.model_dump() for item in imported.transactions] == before
    assert detect_recurring_transactions(list(reversed(imported.transactions)), category_map) == result
    response = client.post("/api/transactions/upload", files={"file": (filename, content)})
    assert response.status_code == 200
    assert client.get("/api/transactions/recurring").json() == result.model_dump(mode="json")
    assert client.post("/api/transactions/categorize").status_code == 200
    assert client.get("/api/transactions/recurring").json() == result.model_dump(mode="json")
    assert client.get("/api/transactions/recurring").json() == result.model_dump(mode="json")
    assert client.get("/api/transactions").json() == response.json()["transactions"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
