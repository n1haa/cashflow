"""Синтетические данные воспроизводят структуру C071/C015 без копирования платежей."""

from io import BytesIO

import pandas as pd
import pytest

from backend.services.file_service import load_transactions_file
from backend.services.transaction_service import normalize_transactions


@pytest.fixture(params=["C071", "C015"])
def dataset_frame(request):
    company = request.param
    return pd.DataFrame([
        {"company_id": company, "operation_id": f"{company}-DEMO-001",
         "operation_date": "2024-10-01", "amount": "1000.25", "direction": "Поступление",
         "counterparty_inn": "0000000001", "counterparty_name": "ООО Демо",
         "payment_purpose": "Тестовая оплата; без реальных данных", "balance_eod": "700.15"},
        {"company_id": company, "operation_id": f"{company}-DEMO-002",
         "operation_date": "2024-10-01", "amount": "300.10", "direction": "Списание",
         "counterparty_inn": "", "counterparty_name": "Демо получатель",
         "payment_purpose": "Тестовое списание", "balance_eod": "700.15"},
        {"company_id": company, "operation_id": f"{company}-DEMO-003",
         "operation_date": "2024-10-02", "amount": "100.00", "direction": "Поступление",
         "counterparty_inn": "000000000002", "counterparty_name": "ИП Демо",
         "payment_purpose": "Тестовый перевод", "balance_eod": "800.15"},
    ])


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_dataset_headers_and_source_mapping(dataset_frame, extension):
    if extension == "csv":
        content = dataset_frame.to_csv(index=False, sep=";").encode("utf-8-sig")
    else:
        buffer = BytesIO()
        dataset_frame.to_excel(buffer, index=False)
        content = buffer.getvalue()
    frame = load_transactions_file(content, f"demo.{extension}")
    assert list(frame.columns) == [
        "company_id", "operation_id", "date", "amount", "direction", "inn",
        "counterparty", "description", "balance",
    ]
    result = normalize_transactions(frame)
    assert (result.total_transactions, result.valid_transactions, result.invalid_transactions) == (3, 2, 1)
    assert result.status == "partial_success"
    assert result.transactions[0].inn == "0000000001"
    assert result.transactions[1].inn is None
    assert result.transactions[2].inn == "000000000002"
    assert result.errors[0].source_row == 3
    assert result.errors[0].operation_id == dataset_frame.iloc[1]["operation_id"]
    assert result.errors[0].company_id == dataset_frame.iloc[1]["company_id"]
    assert [source.source_row for source in result.source_rows] == [2, 3, 4]


def test_dataset_api_keeps_all_rows(client, dataset_frame):
    content = dataset_frame.to_csv(index=False, sep=";").encode("utf-8-sig")
    response = client.post("/api/transactions/upload", files={"file": ("demo.csv", content)})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_success"
    assert body["total_transactions"] == len(body["transactions"]) == 3
    assert body["valid_transactions"] == 2
    assert body["invalid_transactions"] == len(body["errors"]) == 1
    assert body["transactions"][1]["inn"] is None
    assert body["transactions"][0]["amount"] == "1000.25"
    assert body["transactions"][0]["balance"] == "700.15"
    assert body["transactions"][0]["description"] == dataset_frame.iloc[0]["payment_purpose"]
    assert [item["direction"] for item in body["transactions"]] == ["income", "expense", "income"]
    assert client.get("/api/transactions").json() == body["transactions"]


@pytest.mark.parametrize("missing", [None, float("nan"), "", "   "])
def test_empty_inn_is_explicit_error_without_dropping(frame, missing):
    frame.loc[0, "inn"] = missing
    result = normalize_transactions(frame)
    assert result.transactions[0].inn is None
    assert result.invalid_transactions == 1
    assert result.valid_transactions == 0
    assert result.errors[0].field == "inn"
    assert result.errors[0].transaction_id == 1
    assert result.errors[0].source_row == 2


def test_empty_inn_duplicates_are_retained(dataset_frame):
    row = dataset_frame.iloc[[1]]
    result = normalize_transactions(pd.concat([row, row], ignore_index=True))
    assert len(result.transactions) == 2
    assert result.invalid_transactions == 2
    assert result.duplicates == 1
    assert result.duplicate_groups[0].transaction_ids == [1, 2]


def test_source_rows_after_blank_csv_record(dataset_frame):
    content = dataset_frame.to_csv(index=False, sep=";")
    header, rest = content.split("\n", 1)
    frame = load_transactions_file(f"{header}\n\n{rest}".encode(), "demo.csv")
    result = normalize_transactions(frame)
    assert [source.source_row for source in result.source_rows] == [3, 4, 5]
    assert result.errors[0].source_row == 4


def test_conflicting_dataset_aliases(dataset_frame):
    dataset_frame["date"] = dataset_frame["operation_date"]
    with pytest.raises(ValueError, match="неоднозначные"):
        normalize_transactions(dataset_frame)


def test_malformed_inn_still_rejected(dataset_frame):
    dataset_frame.loc[0, "counterparty_inn"] = "invalid"
    with pytest.raises(ValueError, match="Некорректный ИНН.*строке 2"):
        normalize_transactions(dataset_frame)
