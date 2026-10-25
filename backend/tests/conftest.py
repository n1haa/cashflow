from io import BytesIO

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Settings
from backend.database import Base


def pytest_addoption(parser):
    parser.addoption("--dataset-dir", help="Каталог с исходными C071.csv и C015.csv (только чтение)")


@pytest.fixture
def row():
    return {
        "date": "2026-10-01", "amount": "15 000,50", "direction": "Поступление",
        "counterparty": " ООО  Демо ", "inn": "0000000001",
        "description": " Оплата  услуг ", "balance": "-500,25",
    }


@pytest.fixture
def frame(row):
    return pd.DataFrame([row])


@pytest.fixture
def csv_content(frame):
    return frame.to_csv(index=False, sep=";").encode("utf-8-sig")


@pytest.fixture
def xlsx_content(frame):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False)
        pd.DataFrame({"ignored": [1]}).to_excel(writer, sheet_name="Second", index=False)
    return buffer.getvalue()


@pytest.fixture
def auth_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_ALLOW_FINANCIAL_DATA", "false")
    app = create_app(Settings(database_url=f"sqlite:///{(tmp_path / 'auth.db').as_posix()}", app_origin="http://testserver"))
    Base.metadata.create_all(app.state.engine)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def anonymous_client(auth_app):
    with TestClient(auth_app, headers={"X-Requested-With": "CashFlow"}) as test_client:
        yield test_client


@pytest.fixture
def client(anonymous_client):
    test_client = anonymous_client
    response = test_client.post("/api/auth/register", json={
        "name": "Тестовый пользователь", "email": "tester@example.com",
        "password": "test-password-123", "password_confirmation": "test-password-123",
    })
    assert response.status_code == 201
    test_client.get("/api/auth/me")
    test_client.workspace = test_client.app.state.workspaces[response.json()["id"]]
    yield test_client
