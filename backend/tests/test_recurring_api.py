from io import BytesIO

import pandas as pd
import pytest


def test_empty_store(client):
    result = client.get("/api/transactions/recurring")
    assert result.status_code == 200
    assert result.json()["total_transactions"] == result.json()["total_groups"] == 0
    assert result.json()["groups"] == []


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_uploaded_transactions_with_or_without_saved_categories(client, row, extension):
    frame = pd.DataFrame([{**row, "date": day, "description": "Оплата аренды офиса", "direction": "Списание"}
                          for day in ["2026-01-05", "2026-02-05", "2026-03-05"]])
    if extension == "csv":
        content = frame.to_csv(index=False, sep=";").encode("utf-8-sig")
    else:
        buffer = BytesIO()
        frame.to_excel(buffer, index=False)
        content = buffer.getvalue()
    upload = client.post("/api/transactions/upload", files={"file": (f"demo.{extension}", content)})
    assert upload.status_code == 200
    before = client.get("/api/transactions").json()
    result = client.get("/api/transactions/recurring")
    assert result.status_code == 200
    group = result.json()["groups"][0]
    assert group["category"] == "rent"
    assert group["status"] == "confirmed"
    assert group["average_amount"] == "15000.50"
    assert group["next_expected_date"] == "2026-04-05"
    assert group["transaction_ids"] == [1, 2, 3]
    assert client.app.state.categorizations == {}  # GET не записывает категории.
    client.post("/api/transactions/categorize")
    saved_categories = dict(client.app.state.categorizations)
    assert client.get("/api/transactions/recurring").json() == result.json()
    assert client.get("/api/transactions/recurring").json() == result.json()
    assert client.app.state.categorizations == saved_categories
    assert client.get("/api/transactions").json() == before


def test_new_upload_cannot_return_old_groups(client, csv_content):
    client.post("/api/transactions/upload", files={"file": ("demo.csv", csv_content)})
    assert client.get("/api/transactions/recurring").json()["total_transactions"] == 1
    assert client.get("/api/transactions/recurring").json()["groups"] == []
