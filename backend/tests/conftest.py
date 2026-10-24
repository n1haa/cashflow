from io import BytesIO

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


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
def client():
    with TestClient(create_app()) as test_client:
        yield test_client
