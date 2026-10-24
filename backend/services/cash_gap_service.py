"""Анализ готового ForecastResult. Не строит прогноз и не изменяет его значения."""

from datetime import date, timedelta
from decimal import Decimal, localcontext

from backend.schemas import CashGap, CashGapAnalysis, ForecastDay, ForecastResult

CONTEXT_DAYS = 7


def _unavailable(forecast: ForecastResult | None, reason: str, daily: list[ForecastDay] | None = None) -> CashGapAnalysis:
    balance = forecast.summary.opening_balance if forecast else None
    if not isinstance(balance, Decimal) or not balance.is_finite():
        balance = None
    return CashGapAnalysis(
        forecast_method=forecast.method if forecast else None,
        status="not_available", has_cash_gap=None,
        start_date=forecast.start_date if forecast and isinstance(forecast.start_date, date) else None,
        end_date=forecast.end_date if forecast and isinstance(forecast.end_date, date) else None,
        analyzed_days=0, opening_balance=balance, opening_balance_negative=balance < 0 if balance is not None else None,
        gap_count=None, ongoing_count=None, first_gap_date=None, minimum_balance=None,
        maximum_deficit=None, total_deficit_days=None, gaps=[], daily=daily or [],
        warnings=[*(forecast.warnings if forecast else []), reason],
    )


def _group(forecast: ForecastResult, days: list[ForecastDay], begin: int, stop: int, gap_id: int) -> CashGap:
    negative_days = days[begin:stop]
    first, last = negative_days[0], negative_days[-1]
    recovery = days[stop] if stop < len(days) else None
    minimum = min(negative_days, key=lambda item: (item.balance, item.date))
    context_start = max(0, begin - CONTEXT_DAYS)
    preceding = days[context_start:begin]
    expense_days = [item for item in [*preceding, first] if item.expense > 0]
    largest_expense = min(expense_days, key=lambda item: (-item.expense, item.date)) if expense_days else None
    related = sorted(
        (event for event in forecast.scheduled_events
         if days[context_start].date <= event.date <= (recovery.date if recovery else last.date)),
        key=lambda item: (item.date, item.group_id, item.direction, item.category, item.amount),
    )
    return CashGap(
        gap_id=gap_id, status="recovered" if recovery else "ongoing", start_date=first.date,
        last_negative_date=last.date, recovery_date=recovery.date if recovery else None,
        duration_days=(last.date - first.date).days + 1,
        start_balance=first.balance, minimum_balance=minimum.balance, minimum_balance_date=minimum.date,
        maximum_deficit=minimum.balance.copy_abs(),
        starts_before_forecast=begin == 0 and forecast.summary.opening_balance < 0,
        start_day=first, recovery_day=recovery, preceding_days=preceding,
        largest_expense_day=largest_expense, related_events=related,
        regular_expense_categories=sorted({event.category for event in related if event.direction == "expense"}),
    )


def analyze_cash_gaps(forecast: ForecastResult | None) -> CashGapAnalysis:
    """None в счетчиках означает невозможность анализа, а не отсутствие разрывов."""
    if forecast is None or not forecast.daily:
        return _unavailable(forecast, "Нет ежедневных прогнозных данных")
    raw = forecast.daily
    if not all(type(item.date) is date for item in raw) or type(forecast.start_date) is not date or type(forecast.end_date) is not date:
        return _unavailable(forecast, "Некорректные даты прогноза")
    days = sorted(raw, key=lambda item: item.date)
    if (
        type(forecast.horizon_days) is not int or forecast.horizon_days <= 0 or len(days) != forecast.horizon_days
        or (forecast.end_date - forecast.start_date).days + 1 != forecast.horizon_days
        or days[0].date != forecast.start_date or days[-1].date != forecast.end_date
        or any((right.date - left.date).days != 1 for left, right in zip(days, days[1:]))
    ):
        return _unavailable(forecast, "Прогноз содержит пропуски, повторяющиеся даты или неполный заявленный период")
    fields = ("income", "expense", "net_cash_flow", "cumulative_cash_flow", "balance",
              "regular_income", "regular_expense", "historical_income", "historical_expense")
    if any(getattr(item, field) is None for item in days for field in fields if field != "balance"):
        return _unavailable(forecast, "Отсутствуют обязательные дневные денежные показатели")
    values = [getattr(item, field) for item in days for field in fields if getattr(item, field) is not None]
    values.extend(value for value in forecast.summary.model_dump().values() if value is not None)
    values.extend(event.amount for event in forecast.scheduled_events)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        return _unavailable(forecast, "Денежные значения прогноза должны быть конечными числами Decimal")
    if any(getattr(item, field) < 0 for item in days
           for field in ("income", "expense", "regular_income", "regular_expense", "historical_income", "historical_expense")):
        return _unavailable(forecast, "Поступления и расходы должны быть неотрицательными")
    if any(type(event.date) is not date or not forecast.start_date <= event.date <= forecast.end_date or event.amount <= 0
           for event in forecast.scheduled_events):
        return _unavailable(forecast, "Некорректные даты или суммы регулярных прогнозных событий")
    if forecast.summary.opening_balance is None or any(item.balance is None for item in days):
        return _unavailable(forecast, "Неизвестен исходный или ежедневный остаток; отрицательный денежный поток сам по себе не доказывает кассовый разрыв", days)
    with localcontext() as context:
        context.prec = max(50, max(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in values) + len(str(len(days))) + 20)
        # Только проверка согласованности готовых показателей, без их исправления/пересчета.
        previous_balance = forecast.summary.opening_balance
        cumulative = Decimal(0)
        for item in days:
            cumulative += item.net_cash_flow
            if (item.net_cash_flow != item.income - item.expense
                    or item.income != item.regular_income + item.historical_income
                    or item.expense != item.regular_expense + item.historical_expense
                    or item.balance != previous_balance + item.net_cash_flow
                    or item.cumulative_cash_flow != cumulative):
                return _unavailable(forecast, "Дневные показатели и остатки готового прогноза не согласованы")
            previous_balance = item.balance
        if (forecast.summary.total_income != sum((item.income for item in days), Decimal(0))
                or forecast.summary.total_expense != sum((item.expense for item in days), Decimal(0))
                or forecast.summary.net_cash_flow != cumulative
                or forecast.summary.closing_balance != days[-1].balance):
            return _unavailable(forecast, "Сводка готового прогноза не согласована с ежедневными данными")
        gaps = []
        begin = None
        for index, day in enumerate(days):
            if day.balance < 0 and begin is None:
                begin = index
            elif day.balance >= 0 and begin is not None:
                gaps.append(_group(forecast, days, begin, index, len(gaps) + 1))
                begin = None
        if begin is not None:
            gaps.append(_group(forecast, days, begin, len(days), len(gaps) + 1))
        minimum = min(item.balance for item in days)
        warnings = list(forecast.warnings)
        if forecast.horizon_days < 30:
            warnings.append("Проанализирован короткий заявленный горизонт; выводы относятся только к доступным датам")
        if forecast.summary.opening_balance < 0:
            warnings.append("Исходный остаток уже отрицательный; фактическая дата начала дефицита до прогноза неизвестна")
        if any(item.historical_income or item.historical_expense for item in days):
            warnings.append("Для исторической части доступны дневные агрегаты; категории конкретных платежей по дням не установлены")
        warnings.append("Связанные события и дневные потоки влияют на прогнозируемый остаток; причинность отдельных операций и внутридневная очередность не установлены")
        return CashGapAnalysis(
            forecast_method=forecast.method, status="detected" if gaps else "no_gaps", has_cash_gap=bool(gaps),
            start_date=forecast.start_date, end_date=forecast.end_date, analyzed_days=len(days),
            opening_balance=forecast.summary.opening_balance, opening_balance_negative=forecast.summary.opening_balance < 0,
            gap_count=len(gaps), ongoing_count=sum(gap.status == "ongoing" for gap in gaps),
            first_gap_date=gaps[0].start_date if gaps else None, minimum_balance=minimum,
            maximum_deficit=min(minimum, Decimal(0)).copy_abs(), total_deficit_days=sum(gap.duration_days for gap in gaps),
            gaps=gaps, daily=days, warnings=warnings,
        )
