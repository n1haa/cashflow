"""Нормализация и проверка операций. Деньги обрабатываются через Decimal."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from pydantic import ValidationError

from backend.schemas import DuplicateGroup, ImportResult, RowError, SourceRow, Transaction
from backend.services.file_service import FIELD_LABELS, normalize_columns
from backend.services.balance_service import resolve_statement_balance

DIRECTIONS = {
    "income": "income", "поступление": "income", "приход": "income", "кредит": "income",
    "expense": "expense", "списание": "expense", "расход": "expense", "дебет": "expense",
}


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).split())


def parse_money(value: object) -> Decimal:
    text = clean_text(value)
    # Разделители тысяч — группы ровно по три цифры; дробная часть — точка или запятая.
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?: [0-9]{3})+)(?:[.,][0-9]+)?", text):
        raise ValueError("Ожидается число, например 15000.50 или 15 000,50")
    try:
        result = Decimal(text.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError("Некорректное денежное значение") from None
    if not result.is_finite():
        raise ValueError("Денежное значение должно быть конечным")
    return result


def parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError("Некорректная дата операции; используйте YYYY-MM-DD или ДД.ММ.ГГГГ")


def _amount_and_direction(row: pd.Series) -> tuple[Decimal, str]:
    if "direction" in row.index:
        direction = DIRECTIONS.get(clean_text(row["direction"]).casefold())
        if direction is None:
            raise ValueError("Не удалось определить направление операции")
        amount = parse_money(row["amount"])
    else:
        income = parse_money(row["income"]) if clean_text(row["income"]) else Decimal(0)
        expense = parse_money(row["expense"]) if clean_text(row["expense"]) else Decimal(0)
        if income < 0 or expense < 0 or (income > 0) == (expense > 0):
            raise ValueError("Должна быть заполнена ровно одна положительная сумма: поступление или списание")
        amount, direction = (income, "income") if income > 0 else (expense, "expense")
        if "amount" in row.index and parse_money(row["amount"]) != amount:
            raise ValueError("Сумма операции не совпадает с поступлением или списанием")
    if amount <= 0:
        raise ValueError("Сумма операции должна быть больше нуля")
    return amount, direction


def _normalize_row(row: pd.Series, transaction_id: int) -> Transaction:
    operation_date = parse_date(row["date"])
    amount, direction = _amount_and_direction(row)
    text_fields = {field: clean_text(row[field]) for field in ("counterparty", "inn", "description")}
    text_fields["description"] = text_fields["description"] or "unknown"
    for field, value in text_fields.items():
        if not value and field != "inn":
            raise ValueError(f"Не заполнено обязательное поле: {FIELD_LABELS[field]}")
    if text_fields["inn"] and not re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})", text_fields["inn"]):
        raise ValueError("Некорректный ИНН: ожидается строка из 10 или 12 цифр")
    try:
        balance = parse_money(row["balance"]) if clean_text(row.get("balance")) else None
    except ValueError:
        raise ValueError("Некорректный остаток на счете") from None
    return Transaction(
        id=transaction_id, date=operation_date, amount=amount, direction=direction,
        balance=balance, **{**text_fields, "inn": text_fields["inn"] or None},
    )


def _duplicate_groups(transactions: list[Transaction]) -> list[DuplicateGroup]:
    groups: dict[tuple, list[int]] = {}
    for item in transactions:
        key = (item.date, item.amount, item.direction, item.counterparty, item.description)
        groups.setdefault(key, []).append(item.id)
    return [DuplicateGroup(transaction_ids=ids) for ids in groups.values() if len(ids) > 1]


def _date_warnings(transactions: list[Transaction]) -> list[str]:
    warnings = []
    dates = [item.date for item in transactions]
    if dates != sorted(dates):
        warnings.append("Операции расположены не по возрастанию даты; исходный порядок сохранен")
    if len(dates) != len(set(dates)):
        warnings.append("Есть несколько операций за один день; остаток на конец дня не сверяется после каждой операции")
    warnings.append("Арифметическая сверка остатков не выполнялась: не известны начальный остаток и полнота выписки")
    return warnings


def normalize_transactions(frame: pd.DataFrame) -> ImportResult:
    """Сохраняет пустой ИНН с диагностикой; остальные ошибки отклоняют импорт."""
    frame = normalize_columns(frame)
    if frame.empty:
        raise ValueError("Файл не содержит таблицу операций")
    transactions = []
    source_rows = []
    errors = []
    for transaction_id, (source_index, row) in enumerate(frame.iterrows(), start=1):
        try:
            transactions.append(_normalize_row(row, transaction_id))
        except (ValueError, ValidationError) as error:
            message = "Некорректные данные операции" if isinstance(error, ValidationError) else str(error)
            raise ValueError(f"{message} в строке {source_index + 2}") from None
        source = SourceRow(
            transaction_id=transaction_id, source_row=source_index + 2,
            operation_id=clean_text(row.get("operation_id")) or None,
            company_id=clean_text(row.get("company_id")) or None,
        )
        source_rows.append(source)
        if transactions[-1].inn is None:
            errors.append(RowError(**source.model_dump()))
    groups = _duplicate_groups(transactions)
    explicit = None
    explicit_issue = None
    if "statement_opening" in frame.columns:
        try:
            values = {parse_money(value) for value in frame["statement_opening"] if clean_text(value)}
            if len(values) > 1:
                explicit_issue = "В столбце начального остатка указаны разные значения"
            elif values:
                explicit = next(iter(values))
        except ValueError:
            explicit_issue = "Некорректное значение начального остатка"
    balance_info = resolve_statement_balance(transactions, kind=frame.attrs.get("balance_kind", "end_of_day"), explicit_opening=explicit)
    if explicit_issue:
        balance_info = balance_info.model_copy(update={"status": "invalid", "source": "unavailable", "message": explicit_issue,
                                                       "statement_opening_balance": None, "statement_closing_balance": None, "issues": [explicit_issue], "explicit_opening_invalid": True})
    date_warnings = _date_warnings(transactions)
    date_warnings = [message for message in date_warnings if not message.startswith("Арифметическая сверка")]
    date_warnings.append(balance_info.message)
    return ImportResult(
        status="partial_success" if errors else "success",
        message="Выписка обработана с ошибками данных" if errors else "Выписка успешно обработана",
        total_transactions=len(transactions), valid_transactions=len(transactions) - len(errors),
        invalid_transactions=len(errors), errors=errors, source_rows=source_rows,
        duplicates=sum(len(group.transaction_ids) - 1 for group in groups),
        duplicate_groups=groups, warnings=date_warnings, transactions=transactions, balance_info=balance_info,
    )
