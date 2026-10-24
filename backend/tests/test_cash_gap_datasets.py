from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from backend.services.cash_gap_service import analyze_cash_gaps
from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.forecast_service import forecast_cash_flow
from backend.services.transaction_service import normalize_transactions


@pytest.mark.parametrize("filename,total,minimum", [
    ("C071.csv", 614, "2022734.55"),
    ("C015.csv", 2455, "429517.41"),
], ids=["C071-cash-gaps", "C015-cash-gaps"])
@pytest.mark.parametrize("as_of", [date(2026, 10, 24), date(2026, 9, 30)], ids=["unknown-current-balance", "known-historical-balance"])
def test_full_dataset_cash_gaps(request, client, filename, total, minimum, as_of):
    directory = request.config.getoption("--dataset-dir")
    if directory is None:
        pytest.skip("Для проверки оригиналов укажите --dataset-dir")
    path = Path(directory) / filename
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    imported = normalize_transactions(load_transactions_file(content, filename))
    assert len(imported.transactions) == total
    categories = categorize_transactions(imported.transactions)
    category_map = {item.transaction_id: item for item in categories.categorizations}
    forecast = forecast_cash_flow(imported.transactions, as_of=as_of, categorizations=category_map)
    before = forecast.model_dump()
    result = analyze_cash_gaps(forecast)
    assert len(result.daily) == 30
    assert result.start_date == forecast.start_date
    assert result.end_date == forecast.end_date
    assert result.daily == forecast.daily
    if as_of == date(2026, 10, 24):
        assert result.status == "not_available"
        assert result.has_cash_gap is None
        assert result.gap_count is result.ongoing_count is result.maximum_deficit is result.total_deficit_days is None
        assert result.opening_balance is None
        assert all(day.balance is None for day in result.daily)
    else:
        assert result.status == "no_gaps"
        assert result.has_cash_gap is False
        assert result.gap_count == result.ongoing_count == result.total_deficit_days == 0
        assert result.maximum_deficit == 0
        assert result.minimum_balance == Decimal(minimum)
        assert result.minimum_balance == min(day.balance for day in forecast.daily)
        assert result.first_gap_date is None
        assert result.gaps == []
        assert [day.date for day in forecast.daily if day.balance < 0] == []
    assert forecast.model_dump() == before
    assert analyze_cash_gaps(forecast) == result
    uploaded = client.post("/api/transactions/upload", files={"file": (filename, content)})
    assert uploaded.status_code == 200
    assert client.post("/api/transactions/categorize").status_code == 200
    categories_before = dict(client.app.state.categorizations)
    recurring_before = client.get("/api/transactions/recurring").json()
    forecast_before = client.get("/api/forecast", params={"as_of": str(as_of)}).json()
    response = client.get("/api/forecast/cash-gaps", params={"as_of": str(as_of)})
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert client.get("/api/forecast", params={"as_of": str(as_of)}).json() == forecast_before
    assert client.get("/api/transactions/recurring").json() == recurring_before
    assert client.get("/api/transactions").json() == uploaded.json()["transactions"]
    assert client.app.state.categorizations == categories_before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
