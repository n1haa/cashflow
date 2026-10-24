from io import BytesIO

import pandas as pd
import pytest

from backend.services.file_service import MAX_FILE_SIZE, load_transactions_file


@pytest.mark.parametrize("separator", [";", ","])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_csv_formats(frame, separator, encoding):
    content = frame.to_csv(index=False, sep=separator).encode(encoding)
    loaded = load_transactions_file(content, "DEMO.CSV")
    assert loaded.iloc[0]["inn"] == "0000000001"
    assert loaded.iloc[0]["amount"] == "15 000,50"


def test_xlsx_first_sheet(xlsx_content):
    loaded = load_transactions_file(xlsx_content, "demo.xlsx")
    assert len(loaded) == 1
    assert loaded.iloc[0]["inn"] == "0000000001"


@pytest.mark.parametrize("content,filename,error", [
    (b"", "demo.csv", "пустой"),
    (b"abc", "demo.pdf", "CSV и XLSX"),
    (b"broken", "demo.xlsx", "поврежден"),
    (b"\xff\xfe", "demo.csv", "UTF-8"),
    (b"a;b\n1;2\n", "demo.csv", "Дата операции"),
    (b"a;b\n1;2;3\n", "demo.csv", "CSV"),
    (b"a,b;c\n1,2;3\n", "demo.csv", "однозначно"),
    (b" " * (MAX_FILE_SIZE + 1), "demo.csv", "10 MB"),
], ids=["empty", "unsupported", "corrupt-xlsx", "encoding", "missing-columns", "uneven-rows", "ambiguous-csv", "oversized"])
def test_file_errors(content, filename, error):
    with pytest.raises(ValueError, match=error):
        load_transactions_file(content, filename)


def test_headers_only(frame):
    with pytest.raises(ValueError, match="не содержит"):
        load_transactions_file(frame.iloc[:0].to_csv(index=False).encode(), "demo.csv")


def test_empty_xlsx():
    buffer = BytesIO()
    pd.DataFrame().to_excel(buffer, index=False)
    with pytest.raises(ValueError, match="не содержит"):
        load_transactions_file(buffer.getvalue(), "empty.xlsx")


def test_column_aliases(frame):
    renamed = frame.rename(columns={
        "date": " Дата операции ", "counterparty": "Получатель",
        "description": "Назначение платежа", "inn": "ИНН", "balance": "Остаток",
    })
    loaded = load_transactions_file(renamed.to_csv(index=False, sep=";").encode(), "demo.csv")
    assert list(loaded.columns) == list(frame.columns)


def test_ambiguous_aliases(frame):
    frame["Дата"] = frame["date"]
    with pytest.raises(ValueError, match="неоднозначные"):
        load_transactions_file(frame.to_csv(index=False, sep=";").encode(), "demo.csv")


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_identical_headers_rejected(frame, extension):
    frame = pd.concat([frame, frame[["amount"]]], axis=1)
    if extension == "csv":
        content = frame.to_csv(index=False, sep=";").encode()
    else:
        buffer = BytesIO()
        frame.to_excel(buffer, index=False)
        content = buffer.getvalue()
    with pytest.raises(ValueError, match="неоднозначные"):
        load_transactions_file(content, f"demo.{extension}")


def test_size_checked_before_parser(monkeypatch):
    def must_not_run(*args, **kwargs):
        pytest.fail("Парсер не должен вызываться до проверки размера")
    monkeypatch.setattr(pd, "read_csv", must_not_run)
    with pytest.raises(ValueError, match="10 MB"):
        load_transactions_file(b"x" * (MAX_FILE_SIZE + 1), "large.csv")


def test_xlsx_uncompressed_limit(xlsx_content, monkeypatch):
    from backend.services import file_service
    monkeypatch.setattr(file_service, "MAX_XLSX_UNCOMPRESSED_SIZE", 100)
    with pytest.raises(ValueError, match="Распакованный XLSX"):
        load_transactions_file(xlsx_content, "large.xlsx")


def test_blank_lines_preserve_source_row(frame):
    text = frame.to_csv(index=False, sep=";")
    header, data = text.splitlines()
    loaded = load_transactions_file(f"{header}\n\n{data}\n".encode(), "demo.csv")
    assert list(loaded.index) == [1]


def test_xlsx_native_dates_and_numbers(frame):
    frame["date"] = pd.Timestamp("2026-10-01")
    frame["amount"] = 15000.5
    buffer = BytesIO()
    frame.to_excel(buffer, index=False)
    loaded = load_transactions_file(buffer.getvalue(), "demo.xlsx")
    from backend.services.transaction_service import normalize_transactions
    result = normalize_transactions(loaded)
    assert result.transactions[0].date.isoformat() == "2026-10-01"
    assert str(result.transactions[0].amount) == "15000.5"
