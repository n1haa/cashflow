"""Проверка полных внешних файлов; исходные платежи в репозиторий не копируются."""

import csv
import hashlib
from collections import Counter
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from backend.services.file_service import load_transactions_file
from backend.services.transaction_service import normalize_transactions


@pytest.mark.parametrize("filename,total,missing,income,expense,sha256", [
    ("C071.csv", 614, 5, 253, 361, "5cc886333684098ee4717729819e20b1128f22f8e8b48661fa4eec78e015c0ef"),
    ("C015.csv", 2455, 96, 1562, 893, "d3334ca3beeb7ca6ac5986b80c6ccfafd63d1cbd8b1d49a390ed093a0347125e"),
], ids=["C071-full", "C015-full"])
def test_full_dataset(request, client, filename, total, missing, income, expense, sha256):
    directory = request.config.getoption("--dataset-dir")
    if directory is None:
        pytest.skip("Для проверки оригиналов укажите --dataset-dir")
    path = Path(directory) / filename
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == sha256, "Исходный файл отличается от исследованного"
    raw = list(csv.DictReader(StringIO(content.decode("utf-8-sig")), delimiter=";"))
    assert len(raw) == total
    assert len({row["operation_id"] for row in raw}) == total
    result = normalize_transactions(load_transactions_file(content, filename))
    assert result.status == "partial_success"
    assert result.total_transactions == len(result.transactions) == total
    assert result.valid_transactions == total - missing
    assert result.invalid_transactions == len(result.errors) == missing
    assert result.duplicates == 0
    assert result.duplicate_groups == []
    assert Counter(item.direction for item in result.transactions) == {"income": income, "expense": expense}

    for number, (original, item, source) in enumerate(zip(raw, result.transactions, result.source_rows), start=1):
        assert item.id == source.transaction_id == number
        assert source.source_row == number + 1
        assert source.operation_id == original["operation_id"]
        assert source.company_id == original["company_id"]
        assert item.date == date.fromisoformat(original["operation_date"])
        assert item.amount == Decimal(original["amount"])
        assert item.balance == Decimal(original["balance_eod"])
        assert item.direction == ("income" if original["direction"] == "Поступление" else "expense")
        assert item.inn == (original["counterparty_inn"] or None)
        assert item.counterparty == " ".join(original["counterparty_name"].split())
        assert item.description == " ".join(original["payment_purpose"].split())

    assert [error.source_row for error in result.errors] == [
        number + 2 for number, row in enumerate(raw) if not row["counterparty_inn"]
    ]
    # Диагностическая сверка соседних дневных остатков; первый остаток не выводим заново.
    balances = {}
    net = {}
    for item in result.transactions:
        balances.setdefault(item.date, set()).add(item.balance)
        net[item.date] = net.get(item.date, Decimal(0)) + item.amount * (1 if item.direction == "income" else -1)
    assert all(len(values) == 1 for values in balances.values())
    days = sorted(balances)
    for previous, current in zip(days, days[1:]):
        assert next(iter(balances[current])) - next(iter(balances[previous])) == net[current]

    response = client.post("/api/transactions/upload", files={"file": (filename, content)})
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert client.get("/api/transactions").json() == response.json()["transactions"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
