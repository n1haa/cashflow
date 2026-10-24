"""Единый формат операций и результатов импорта."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Transaction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    id: int = Field(gt=0)
    date: date
    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    direction: Literal["income", "expense"]
    counterparty: str = Field(min_length=1)
    # Пустой ИНН сохраняется как отсутствие данных и сопровождается RowError.
    inn: str | None = Field(pattern=r"^(?:[0-9]{10}|[0-9]{12})$")
    description: str = Field(min_length=1)
    balance: Decimal = Field(allow_inf_nan=False)


class DuplicateGroup(BaseModel):
    transaction_ids: list[int]


class SourceRow(BaseModel):
    transaction_id: int
    source_row: int
    operation_id: str | None = None
    company_id: str | None = None


class RowError(SourceRow):
    field: Literal["inn"] = "inn"
    message: str = "Не заполнен ИНН; операция сохранена без подстановки значения"


class ImportResult(BaseModel):
    status: Literal["success", "partial_success"] = "success"
    message: str = "Выписка успешно обработана"
    total_transactions: int
    valid_transactions: int
    invalid_transactions: int = 0
    duplicates: int
    duplicate_groups: list[DuplicateGroup]
    warnings: list[str]
    transactions: list[Transaction]
    source_rows: list[SourceRow] = Field(default_factory=list)
    errors: list[RowError] = Field(default_factory=list)


class Categorization(BaseModel):
    """Метаданные по ID существующей операции; финансовые поля не дублируются."""

    transaction_id: int
    category: str = "unknown"
    category_name: str = "Не определено"
    source: str
    status: Literal["automatic", "undefined", "needs_review"] = "undefined"
    is_automatic: bool = False
    matched_rules: list[str] = Field(default_factory=list)
    reason: str


class CategorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_ids: list[int] | None = None


class CategorizationResult(BaseModel):
    total_transactions: int
    categorized_transactions: int
    undefined_transactions: int
    needs_review_transactions: int
    automatic_percentage: float
    distribution: dict[str, int]
    categorizations: list[Categorization]


class RecurringOperation(BaseModel):
    group_id: str
    description: str
    counterparty: str
    category: str
    category_name: str
    direction: Literal["income", "expense"]
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "yearly", "unknown"]
    status: Literal["confirmed", "potential", "uncertain"]
    transaction_ids: list[int]
    repetitions: int
    distinct_dates: int
    first_date: date
    last_date: date
    next_expected_date: date | None
    average_amount: Decimal
    minimum_amount: Decimal
    maximum_amount: Decimal
    average_interval_days: Decimal
    date_exceptions: int
    interval_exceptions: int
    amount_variable: bool
    usable_for_calculations: bool
    reason: str


class RecurringResult(BaseModel):
    algorithm_version: str = "recurrence-v1"
    total_transactions: int
    total_groups: int
    confirmed_groups: int
    potential_groups: int
    uncertain_groups: int
    frequency_distribution: dict[str, int]
    ungrouped_transaction_ids: list[int]
    groups: list[RecurringOperation]
