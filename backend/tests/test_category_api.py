import pytest


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_categorize_uploaded_operations(client, csv_content, xlsx_content, extension):
    content = csv_content if extension == "csv" else xlsx_content
    imported = client.post("/api/transactions/upload", files={"file": (f"demo.{extension}", content)})
    assert imported.status_code == 200
    before = client.get("/api/transactions").json()
    response = client.post("/api/transactions/categorize")
    assert response.status_code == 200
    body = response.json()
    assert body["total_transactions"] == 1
    assert body["categorizations"][0]["category"] == "customer_receipts"
    assert body["categorizations"][0]["transaction_id"] == before[0]["id"]
    assert client.post("/api/transactions/categorize").json() == body
    assert client.get("/api/transactions").json() == before
    assert list(client.app.state.categorizations) == [1]


def test_empty_store(client):
    response = client.post("/api/transactions/categorize")
    assert response.status_code == 200
    assert response.json()["total_transactions"] == 0


def test_selection_and_validation(client, csv_content):
    client.post("/api/transactions/upload", files={"file": ("demo.csv", csv_content)})
    assert client.post("/api/transactions/categorize", json={"transaction_ids": [1]}).json()["total_transactions"] == 1
    assert client.post("/api/transactions/categorize", json={"transaction_ids": []}).json()["total_transactions"] == 0
    before = dict(client.app.state.categorizations)
    assert client.post("/api/transactions/categorize", json={"transaction_ids": [1, 1]}).status_code == 400
    assert client.post("/api/transactions/categorize", json={"transaction_ids": [2]}).status_code == 404
    assert client.post("/api/transactions/categorize", json={"transaction_ids": "all"}).status_code == 422
    assert client.post("/api/transactions/categorize", json={"unexpected": 1}).status_code == 422
    assert client.app.state.categorizations == before


def test_upload_resets_categories_only_on_success(client, csv_content):
    client.post("/api/transactions/upload", files={"file": ("demo.csv", csv_content)})
    client.post("/api/transactions/categorize")
    before = dict(client.app.state.categorizations)
    assert client.post("/api/transactions/upload", files={"file": ("broken.csv", b"bad")}).status_code == 400
    assert client.app.state.categorizations == before
    assert client.post("/api/transactions/upload", files={"file": ("demo.csv", csv_content)}).status_code == 200
    assert client.app.state.categorizations == {}


def test_missing_inn_does_not_block_categorization(client, csv_content):
    # Операция имеет пустой ИНН, но ее финансовые данные сохраняются Sprint 1.
    content = csv_content.replace(b"0000000001", b"")
    client.post("/api/transactions/upload", files={"file": ("demo.csv", content)})
    before = client.get("/api/transactions").json()
    response = client.post("/api/transactions/categorize")
    assert response.status_code == 200
    assert response.json()["categorized_transactions"] == 1
    assert before[0]["inn"] is None
    assert client.get("/api/transactions").json() == before


def test_unknown_metadata_without_loss(client, csv_content):
    content = csv_content.replace("Оплата  услуг".encode(), "Неизвестная операция".encode())
    client.post("/api/transactions/upload", files={"file": ("demo.csv", content)})
    before = client.get("/api/transactions").json()
    response = client.post("/api/transactions/categorize")
    assert response.status_code == 200
    body = response.json()
    assert body["categorized_transactions"] == 0
    assert body["undefined_transactions"] == 1
    assert body["automatic_percentage"] == 0
    assert body["categorizations"][0]["category"] == "unknown"
    assert body["categorizations"][0]["status"] == "undefined"
    assert client.get("/api/transactions").json() == before
