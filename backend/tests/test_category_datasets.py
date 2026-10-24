"""Проверка категоризации всех операций обоих оригиналов, без копий платежей."""

import hashlib
from pathlib import Path

import pytest

from backend.services.file_service import load_transactions_file
from backend.services.transaction_service import normalize_transactions
from backend.services.category_service import categorize_transactions


@pytest.mark.parametrize("filename,expected", [
    ("C071.csv", {"bank_fees": 24, "business_services": 48, "customer_receipts": 253,
                  "marketing": 57, "own_transfers": 33, "payroll": 48, "rent": 24,
                  "software": 46, "taxes": 33, "telecom": 24, "utilities": 24}),
    ("C015.csv", {"business_services": 24, "contractors": 416, "equipment_rent": 24,
                  "marketing": 49, "other_income": 1, "owner_withdrawals": 42,
                  "payroll": 96, "rent": 24, "sales": 1561, "software": 21,
                  "supplies": 73, "taxes": 28, "telecom": 24, "utilities": 72}),
], ids=["C071-categories", "C015-categories"])
def test_full_dataset_categorization(request, client, filename, expected):
    directory = request.config.getoption("--dataset-dir")
    if directory is None:
        pytest.skip("Для проверки оригиналов укажите --dataset-dir")
    path = Path(directory) / filename
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    imported = normalize_transactions(load_transactions_file(content, filename))
    originals = [item.model_dump() for item in imported.transactions]
    result = categorize_transactions(imported.transactions)
    assert result.distribution == expected  # Регрессия правил v1, не измерение точности.
    assert result.total_transactions == result.categorized_transactions == sum(expected.values())
    assert result.undefined_transactions == 0
    assert result.automatic_percentage == 100
    assert [item.transaction_id for item in result.categorizations] == [item.id for item in imported.transactions]
    assert [item.model_dump() for item in imported.transactions] == originals
    for item, category in zip(imported.transactions, result.categorizations):
        text = item.description.casefold()
        if text.startswith("зачисление") or text.startswith("инкассированная выручка"):
            assert category.category == "sales"  # Комиссии/возвраты в реестре не меняют тип зачисления.
        if "гпх" in text:
            assert category.category == "contractors"
        if text.startswith("комиссия за ведение счета"):
            assert category.category == "bank_fees"
        if text.startswith("перевод"):
            assert category.category in {"own_transfers", "owner_withdrawals"}

    upload = client.post("/api/transactions/upload", files={"file": (filename, content)})
    assert upload.status_code == 200
    assert upload.json()["status"] == "partial_success"  # Пустые ИНН остаются диагностикой Sprint 1.
    response = client.post("/api/transactions/categorize")
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert client.post("/api/transactions/categorize").json() == response.json()
    assert len(client.app.state.categorizations) == result.total_transactions
    assert client.get("/api/transactions").json() == upload.json()["transactions"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
