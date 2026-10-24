"""Чтение таблицы и проверка структуры без HTTP-логики."""

import csv
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pandas as pd

MAX_FILE_SIZE = 10 * 1024 * 1024
# XLSX — архив; ограничиваем также его распакованный размер до чтения Pandas.
MAX_XLSX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024

COLUMN_ALIASES = {
    "date": ("дата", "дата операции", "operation date", "operation_date"),
    "amount": ("сумма", "сумма операции"),
    "direction": ("направление", "направление операции", "тип операции"),
    "counterparty": ("контрагент", "наименование контрагента", "получатель", "плательщик", "counterparty_name"),
    "inn": ("инн", "инн контрагента", "counterparty_inn"),
    "description": ("назначение", "назначение платежа", "описание", "payment_purpose"),
    "balance": ("остаток", "остаток на счете", "остаток на конец дня", "остаток на счете на конец дня", "balance_eod"),
    "income": ("поступление", "поступления", "кредит"),
    "expense": ("списание", "списания", "дебет"),
}
COLUMN_NAMES = {
    " ".join(alias.casefold().split()): canonical
    for canonical, aliases in COLUMN_ALIASES.items()
    for alias in (canonical, *aliases)
}
FIELD_LABELS = {
    "date": "Дата операции", "amount": "Сумма операции",
    "direction": "Направление операции", "counterparty": "Контрагент",
    "inn": "ИНН", "description": "Назначение платежа", "balance": "Остаток",
}


def validate_file_metadata(filename: str, size: int) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        raise ValueError("Поддерживаются только CSV и XLSX")
    if size > MAX_FILE_SIZE:
        raise ValueError("Размер файла превышает 10 MB")
    if size == 0:
        raise ValueError("Файл пустой")
    return extension


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = []
    for column in frame.columns:
        name = " ".join(str(column).strip().casefold().split())
        columns.append(COLUMN_NAMES.get(name, name))
    if len(columns) != len(set(columns)):
        raise ValueError("В файле есть повторяющиеся или неоднозначные столбцы")
    frame = frame.copy()
    frame.columns = columns
    required = ["date", "counterparty", "inn", "description", "balance"]
    split_amounts = {"income", "expense"}.issubset(columns)
    if "direction" in columns or not split_amounts:
        required.append("amount")
    if not split_amounts:
        required.append("direction")
    for field in required:
        if field not in columns:
            raise ValueError(f"В файле отсутствует обязательный столбец: {FIELD_LABELS[field]}")
    return frame


def _read_csv(content: bytes) -> pd.DataFrame:
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV должен быть в кодировке UTF-8 или UTF-8 с BOM") from None
    # Проверяем оба разрешенных разделителя. Не угадываем при неоднозначности.
    candidates = []
    for delimiter in (";", ","):
        try:
            rows = list(csv.reader(StringIO(source), delimiter=delimiter, strict=True))
        except csv.Error:
            continue
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if rows and len(rows[0]) > 1 and all(len(row) == len(rows[0]) for row in rows):
            candidates.append((delimiter, rows[0]))
    if len(candidates) != 1:
        raise ValueError("Не удалось однозначно определить CSV: используйте ; или , и одинаковое число полей в строках")
    delimiter, headers = candidates[0]
    # Pandas переименовывает одинаковые заголовки; проверяем их до чтения.
    normalize_columns(pd.DataFrame(columns=headers))
    return pd.read_csv(
        StringIO(source), sep=delimiter, dtype=str, keep_default_na=False,
        skip_blank_lines=False,
    )


def _read_xlsx(content: bytes) -> pd.DataFrame:
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_XLSX_UNCOMPRESSED_SIZE:
                raise ValueError("Распакованный XLSX превышает допустимый размер 100 MB")
    except BadZipFile:
        raise ValueError("Не удалось прочитать XLSX: файл поврежден") from None
    frame = pd.read_excel(
        BytesIO(content), sheet_name=0, header=None, engine="openpyxl", dtype=str, keep_default_na=False,
    )
    if frame.empty:
        return frame
    frame.columns = frame.iloc[0].tolist()
    return frame.iloc[1:].reset_index(drop=True)


def load_transactions_file(content: bytes, filename: str) -> pd.DataFrame:
    """Принимает содержимое загруженного файла; возвращает таблицу с едиными заголовками."""
    extension = validate_file_metadata(filename, len(content))
    try:
        frame = _read_csv(content) if extension == ".csv" else _read_xlsx(content)
    except ValueError:
        raise
    except Exception:
        # Ошибки сторонних парсеров не должны раскрывать внутренние пути.
        raise ValueError("Не удалось прочитать таблицу: проверьте формат и целостность файла") from None
    if frame.empty:
        raise ValueError("Файл не содержит таблицу операций")
    # Полностью пустые строки не являются операциями; исходный индекс сохраняется.
    nonempty = frame.apply(lambda col: col.map(lambda value: not pd.isna(value) and bool(str(value).strip())))
    frame = frame.loc[nonempty.any(axis=1)]
    if frame.empty:
        raise ValueError("Файл не содержит таблицу операций")
    return normalize_columns(frame)
