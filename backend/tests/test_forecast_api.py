from decimal import Decimal

import pytest


def test_empty_forecast(client):
    response = client.get("/api/forecast", params={"as_of": "2026-01-01"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["daily"]) == 30
    assert body["summary"]["opening_balance"] is None
    assert body["method"] == "recurring_plus_historical_mean_v1"


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_upload_forecast_and_existing_api_unchanged(client, csv_content, xlsx_content, extension):
    content = csv_content if extension == "csv" else xlsx_content
    uploaded = client.post("/api/transactions/upload", files={"file": (f"demo.{extension}", content)})
    assert uploaded.status_code == 200
    client.post("/api/transactions/categorize")
    before_categories = dict(client.app.state.categorizations)
    before_recurring = client.get("/api/transactions/recurring").json()
    response = client.get("/api/forecast", params={"as_of": "2026-10-01"})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["opening_balance"] == "-500.25"
    assert body["summary"]["closing_balance"] == "-500.25"
    assert body["start_date"] == "2026-10-02"
    assert body["end_date"] == "2026-10-31"
    assert client.get("/api/forecast", params={"as_of": "2026-10-01"}).json() == body
    assert client.get("/api/transactions").json() == uploaded.json()["transactions"]
    assert client.app.state.categorizations == before_categories
    assert client.get("/api/transactions/recurring").json() == before_recurring


@pytest.mark.parametrize("params,status", [
    ({"as_of": "not-a-date"}, 422), ({"as_of": "2026-02-30"}, 422),
    ({"opening_balance": "not-a-number"}, 422), ({"opening_balance": "NaN"}, 422),
    ({"as_of": "9999-12-31"}, 400),
])
def test_bad_parameters(client, params, status):
    assert client.get("/api/forecast", params=params).status_code == status


def test_provided_zero_balance_is_known(client):
    body = client.get("/api/forecast", params={"as_of": "2026-01-01", "opening_balance": "0"}).json()
    assert Decimal(body["summary"]["opening_balance"]) == 0
    assert body["opening_balance_source"] == "provided"
