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
    balance: Decimal | None = Field(allow_inf_nan=False)


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


class StatementBalance(BaseModel):
    status: Literal["detected", "provided", "unknown", "invalid"]
    kind: Literal["end_of_day", "after_transaction"] = "end_of_day"
    source: str
    message: str
    statement_start: date | None = None
    statement_end: date | None = None
    statement_opening_balance: Decimal | None = None
    statement_closing_balance: Decimal | None = None
    explicit_opening_balance: Decimal | None = None
    issues: list[str] = Field(default_factory=list)
    explicit_opening_invalid: bool = False


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
    balance_info: StatementBalance | None = None


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
    date_variable: bool = False
    usable_for_calculations: bool
    reason: str


class RecurringResult(BaseModel):
    algorithm_version: str = "recurrence-v2"
    total_transactions: int
    total_groups: int
    confirmed_groups: int
    potential_groups: int
    uncertain_groups: int
    frequency_distribution: dict[str, int]
    ungrouped_transaction_ids: list[int]
    groups: list[RecurringOperation]


CategoryCode = Literal[
    "sales", "customer_receipts", "other_income", "supplies", "payroll", "contractors",
    "rent", "equipment_rent", "taxes", "bank_fees", "marketing", "utilities", "telecom",
    "software", "business_services", "own_transfers", "owner_withdrawals", "refund_in",
    "refund_out", "unknown",
]


class RecurringRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_group_id: str | None = None
    description: str = Field(min_length=1, max_length=200)
    counterparty: str = Field(min_length=1, max_length=200)
    category: CategoryCode
    direction: Literal["income", "expense"]
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "yearly"]
    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    next_date: date
    enabled: bool = True


class RecurringRule(RecurringRuleRequest):
    id: str


class ForecastEvent(BaseModel):
    group_id: str
    date: date
    category: str
    direction: Literal["income", "expense"]
    amount: Decimal


class ForecastDay(BaseModel):
    date: date
    income: Decimal
    expense: Decimal
    net_cash_flow: Decimal
    cumulative_cash_flow: Decimal
    balance: Decimal | None
    regular_income: Decimal
    regular_expense: Decimal
    historical_income: Decimal
    historical_expense: Decimal


class ForecastCategory(BaseModel):
    category: str
    category_name: str
    income: Decimal
    expense: Decimal
    net_cash_flow: Decimal


class ForecastSummary(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    net_cash_flow: Decimal
    opening_balance: Decimal | None
    closing_balance: Decimal | None


class ForecastResult(BaseModel):
    method: str = "recurring_plus_historical_mean_v1"
    as_of: date
    start_date: date
    end_date: date
    horizon_days: int = 30
    history_start: date | None
    history_end: date | None
    history_transactions: int
    history_window_transactions: int
    history_days: int
    history_lag_days: int | None
    opening_balance_source: Literal["provided", "history_eod", "statement_reported", "unavailable"]
    regular_group_ids: list[str]
    excluded_regular_groups: dict[str, str]
    regular_history_transaction_ids: list[int]
    residual_history_transaction_ids: list[int]
    excluded_history_transaction_ids: list[int]
    scheduled_events: list[ForecastEvent]
    daily: list[ForecastDay]
    categories: list[ForecastCategory]
    summary: ForecastSummary
    warnings: list[str]
    balance_info: StatementBalance | None = None
    as_of_source: Literal["explicit", "statement_end", "current_date"] = "explicit"


class CashGap(BaseModel):
    gap_id: int
    status: Literal["ongoing", "recovered"]
    start_date: date
    last_negative_date: date
    recovery_date: date | None
    duration_days: int
    start_balance: Decimal
    minimum_balance: Decimal
    minimum_balance_date: date
    maximum_deficit: Decimal
    starts_before_forecast: bool
    start_day: ForecastDay
    recovery_day: ForecastDay | None
    preceding_days: list[ForecastDay]
    largest_expense_day: ForecastDay | None
    related_events: list[ForecastEvent]
    regular_expense_categories: list[str]


class CashGapAnalysis(BaseModel):
    analysis_version: str = "cash-gaps-v1"
    forecast_method: str | None
    status: Literal["detected", "no_gaps", "not_available"]
    has_cash_gap: bool | None
    start_date: date | None
    end_date: date | None
    analyzed_days: int
    opening_balance: Decimal | None
    opening_balance_negative: bool | None
    gap_count: int | None
    ongoing_count: int | None
    first_gap_date: date | None
    minimum_balance: Decimal | None
    maximum_deficit: Decimal | None
    total_deficit_days: int | None
    gaps: list[CashGap]
    daily: list[ForecastDay]
    warnings: list[str]
