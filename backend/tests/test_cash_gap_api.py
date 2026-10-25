import pytest


def test_empty_store_is_not_available(client):
    response = client.get("/api/forecast/cash-gaps", params={"as_of": "2026-10-24"})
    assert response.status_code == 200
    assert response.json()["status"] == "not_available"
    assert response.json()["has_cash_gap"] is None


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_existing_pipeline_and_one_forecast_call(client, csv_content, xlsx_content, extension, monkeypatch):
    from backend.api import forecast as forecast_api
    content = csv_content if extension == "csv" else xlsx_content
    uploaded = client.post("/api/transactions/upload", files={"file": (f"demo.{extension}", content)})
    assert uploaded.status_code == 200
    client.post("/api/transactions/categorize")
    before_categories = dict(client.workspace.categorizations)
    before_recurring = client.get("/api/transactions/recurring").json()
    original = forecast_api.forecast_cash_flow
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(forecast_api, "forecast_cash_flow", counted)
    response = client.get("/api/forecast/cash-gaps", params={"as_of": "2026-10-01"})
    assert response.status_code == 200
    assert len(calls) == 1
    body = response.json()
    assert body["status"] == "detected"
    assert body["gap_count"] == body["ongoing_count"] == 1
    assert body["maximum_deficit"] == "500.25"
    assert body["gaps"][0]["duration_days"] == 30
    assert body["gaps"][0]["starts_before_forecast"]
    assert client.get("/api/transactions").json() == uploaded.json()["transactions"]
    assert client.workspace.categorizations == before_categories
    assert client.get("/api/transactions/recurring").json() == before_recurring


def test_explicit_zero_balance_is_known(client):
    result = client.get("/api/forecast/cash-gaps", params={"as_of": "2026-10-24", "opening_balance": "0"}).json()
    assert result["status"] == "no_gaps"
    assert result["has_cash_gap"] is False


@pytest.mark.parametrize("params,status", [
    ({"as_of": "2026-02-30"}, 422), ({"opening_balance": "NaN"}, 422),
    ({"as_of": "9999-12-31"}, 400),
])
def test_forecast_parameter_errors_reused(client, params, status):
    assert client.get("/api/forecast/cash-gaps", params=params).status_code == status
