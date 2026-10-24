import pytest

from backend.services.file_service import MAX_FILE_SIZE


def upload(client, content, filename="demo.csv"):
    return client.post("/api/transactions/upload", files={"file": (filename, content)})


def test_upload_and_get(client, csv_content):
    assert client.get("/api/transactions").json() == []
    response = upload(client, csv_content)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["total_transactions"] == body["valid_transactions"] == 1
    assert body["invalid_transactions"] == body["duplicates"] == 0
    assert body["transactions"][0] == {
        "id": 1, "date": "2026-10-01", "amount": "15000.50", "direction": "income",
        "counterparty": "ООО Демо", "inn": "0000000001", "description": "Оплата услуг",
        "balance": "-500.25",
    }
    assert client.get("/api/transactions").json() == body["transactions"]


def test_xlsx_upload(client, xlsx_content):
    response = upload(client, xlsx_content, "demo.xlsx")
    assert response.status_code == 200
    assert response.json()["transactions"][0]["inn"] == "0000000001"


@pytest.mark.parametrize("content,filename,status,error", [
    (b"abc", "demo.txt", 400, "CSV и XLSX"),
    (b"", "demo.csv", 400, "пустой"),
    (b"a;b\n1;2", "demo.csv", 400, "Дата операции"),
    (b"broken", "demo.xlsx", 400, "поврежден"),
    (b"a" * (MAX_FILE_SIZE + 1), "demo.csv", 413, "10 MB"),
], ids=["unsupported", "empty", "missing-columns", "corrupt-xlsx", "oversized"])
def test_upload_errors(client, content, filename, status, error):
    response = upload(client, content, filename)
    assert response.status_code == status
    assert error in response.json()["detail"]
    assert "Traceback" not in response.text


def test_invalid_upload_preserves_previous_data(client, csv_content):
    successful = upload(client, csv_content).json()
    invalid = csv_content.replace(b"2026-10-01", b"2026-02-30")
    response = upload(client, invalid)
    assert response.status_code == 400
    assert "строке 2" in response.json()["detail"]
    assert client.get("/api/transactions").json() == successful["transactions"]


def test_repeated_upload_replaces_data(client, csv_content):
    upload(client, csv_content)
    upload(client, csv_content)
    assert len(client.get("/api/transactions").json()) == 1


def test_no_file(client):
    assert client.post("/api/transactions/upload").status_code == 422


def test_upload_empty_payment_purpose(client, csv_content):
    content = csv_content.replace(" Оплата  услуг ".encode(), b"")
    response = upload(client, content)
    assert response.status_code == 200
    assert response.json()["transactions"][0]["description"] == "unknown"
    assert client.post("/api/transactions/categorize").json()["categorizations"][0]["category"] == "unknown"


def test_sample(client):
    from pathlib import Path
    sample = Path(__file__).resolve().parents[2] / "data" / "sample.csv"
    result = upload(client, sample.read_bytes())
    assert result.status_code == 200
    assert result.json()["total_transactions"] == 4
    assert [item["direction"] for item in result.json()["transactions"]] == ["income", "expense", "income", "expense"]
