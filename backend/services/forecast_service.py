"""30 календарных дней: расписание Sprint 3 + средние остаточных потоков без двойного учета."""

from calendar import monthrange
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from statistics import median_low
from zoneinfo import ZoneInfo

from backend.schemas import (
    Categorization, ForecastCategory, ForecastDay, ForecastEvent, ForecastResult,
    ForecastSummary, RecurringOperation, RecurringRule, StatementBalance, Transaction,
)
from backend.services.category_rules import CATEGORY_LABELS
from backend.services.category_service import categorize_transaction
from backend.services.recurring_service import _calendar_date, detect_recurring_transactions
from backend.services.balance_service import last_day_balance, resolve_statement_balance

HORIZON_DAYS = 30
LOOKBACK_DAYS = 90
MIN_HISTORY_DAYS = 30
MIN_BUCKET_OPERATIONS = 3
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MONTH_STEPS = {"monthly": 1, "quarterly": 3, "yearly": 12}
DAY_STEPS = {"weekly": 7, "biweekly": 14}


def project_date() -> date:
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def _schedule_date(anchor: date, frequency: str, cycles: int, end_of_month: bool, anchor_day: int | None = None) -> date:
    if frequency in DAY_STEPS:
        return anchor + timedelta(days=DAY_STEPS[frequency] * cycles)
    return _calendar_date(anchor, MONTH_STEPS[frequency] * cycles, anchor_day or anchor.day, end_of_month)


def _events(group: RecurringOperation, items: Sequence[Transaction], start: date, end: date) -> list[ForecastEvent]:
    anchor = group.next_expected_date
    end_of_month = all(item.date.day == monthrange(item.date.year, item.date.month)[1] for item in items)
    anchor_day = median_low(sorted(item.date.day for item in items))
    if group.frequency in DAY_STEPS:
        cycles = max(0, (start - anchor).days // DAY_STEPS[group.frequency])
    else:
        months = (start.year - anchor.year) * 12 + start.month - anchor.month
        cycles = max(0, months // MONTH_STEPS[group.frequency])
    result = []
    while True:
        try:
            expected = _schedule_date(anchor, group.frequency, cycles, end_of_month, anchor_day)
        except (OverflowError, ValueError):
            break
        if expected > end:
            break
        if expected >= start:
            result.append(ForecastEvent(group_id=group.group_id, date=expected, category=group.category,
                                        direction=group.direction, amount=group.average_amount))
        cycles += 1
    return result


def _rule_events(rule: RecurringRule, start: date, end: date) -> list[ForecastEvent]:
    anchor = rule.next_date
    end_of_month = anchor.day == monthrange(anchor.year, anchor.month)[1]
    if rule.frequency in DAY_STEPS:
        step_days = DAY_STEPS[rule.frequency]
        cycles = max(0, (start - anchor).days // step_days)
        while anchor + timedelta(days=cycles * step_days) < start:
            cycles += 1
    else:
        step_months = MONTH_STEPS[rule.frequency]
        months = (start.year - anchor.year) * 12 + start.month - anchor.month
        cycles = max(0, months // step_months)
        while _schedule_date(anchor, rule.frequency, cycles, end_of_month) < start:
            cycles += 1
    events = []
    while True:
        try:
            expected = _schedule_date(anchor, rule.frequency, cycles, end_of_month)
        except (OverflowError, ValueError):
            break
        if expected > end:
            break
        events.append(ForecastEvent(
            group_id=rule.id, date=expected, category=rule.category,
            direction=rule.direction, amount=rule.amount,
        ))
        cycles += 1
    return events


def _opening_balance(history: Sequence[Transaction], as_of: date, provided: Decimal | None, info: StatementBalance) -> tuple[Decimal | None, str]:
    if provided is not None:
        return provided, "provided"
    reported = last_day_balance(history, info.kind)
    if history and max(item.date for item in history) == as_of and reported is not None:
        return reported, "history_eod" if info.status in {"detected", "provided"} else "statement_reported"
    if info.status in {"detected", "provided"} and info.statement_end == as_of and info.statement_closing_balance is not None:
        return info.statement_closing_balance, "history_eod"
    return None, "unavailable"


def _build_forecast(
    transactions: Sequence[Transaction], as_of: date, opening_balance: Decimal | None,
    categorizations: Mapping[int, Categorization] | None,
    balance_info: StatementBalance | None,
    recurring_rules: Sequence[RecurringRule],
) -> ForecastResult:
    try:
        start, end = as_of + timedelta(days=1), as_of + timedelta(days=HORIZON_DAYS)
    except OverflowError:
        raise ValueError("Дата расчета не позволяет построить следующие 30 календарных дней") from None
    ids = [item.id for item in transactions]
    if len(set(ids)) != len(ids):
        raise ValueError("Идентификаторы операций должны быть уникальными")
    history = sorted((item for item in transactions if item.date <= as_of), key=lambda item: (item.date, item.id))
    categories = {
        item.id: (categorizations or {}).get(item.id) or categorize_transaction(item)
        for item in history
    }
    recurring = detect_recurring_transactions(history, categories)
    history_end = history[-1].date if history else None
    history_start = max(history[0].date, history_end - timedelta(days=min(LOOKBACK_DAYS - 1, history_end.toordinal() - 1))) if history else None
    window = [item for item in history if item.date >= history_start]
    history_days = (history_end - history_start).days + 1 if history else 0
    warnings = []
    if not history:
        warnings.append("Нет исторических операций на дату расчета; будущие потоки не оценены")
    if len(history) != len(transactions):
        warnings.append("Операции позже даты расчета исключены из анализа")
    if history_end and history_end < as_of:
        warnings.append("История заканчивается раньше даты расчета; средние используют окно до последней известной операции, пропущенные дни не считаются нулевыми")
    info = resolve_statement_balance(history, kind=balance_info.kind if balance_info else "end_of_day",
                                     explicit_opening=balance_info.explicit_opening_balance if balance_info else None)
    if balance_info and balance_info.explicit_opening_invalid:
        reported = last_day_balance(history, balance_info.kind)
        info = info.model_copy(update={"status": "invalid", "source": "statement_reported" if reported is not None else "unavailable",
                                      "message": balance_info.message, "issues": balance_info.issues,
                                      "explicit_opening_invalid": True, "statement_opening_balance": None,
                                      "statement_closing_balance": reported})
    balance, balance_source = _opening_balance(history, as_of, opening_balance, info)
    if info.status in {"invalid", "unknown"}:
        warnings.append(info.message)
    if balance is None:
        warnings.append("Исходный остаток неизвестен или неоднозначен; абсолютный прогноз остатка не рассчитывается")
    by_id = {item.id: item for item in history}
    regular_ids = set()
    regular_groups = []
    excluded_groups = {}
    events = []
    rules_by_group = {rule.source_group_id: rule for rule in recurring_rules if rule.source_group_id}
    managed_ids = set()
    applied_rule_ids = set()
    for group in recurring.groups:
        rule = rules_by_group.get(group.group_id)
        if rule is not None:
            applied_rule_ids.add(rule.id)
            managed_ids.update(group.transaction_ids)
            if rule.enabled:
                regular_groups.append(group.group_id)
                regular_ids.update(group.transaction_ids)
                events.extend(_rule_events(rule, start, end))
            else:
                excluded_groups[group.group_id] = "Отключено пользователем"
            continue
        if group.status != "confirmed" or not group.usable_for_calculations:
            excluded_groups[group.group_id] = "Серия не confirmed либо не пригодна для расчетов по Sprint 3"
            continue
        if group.frequency not in DAY_STEPS and group.frequency not in MONTH_STEPS:
            excluded_groups[group.group_id] = "Неизвестная периодичность"
            continue
        # Не продлеваем давно прекратившиеся серии без ограничения.
        try:
            fresh_until = _schedule_date(group.last_date, group.frequency, 2, False)
        except (OverflowError, ValueError):
            fresh_until = date.max
        if as_of > fresh_until:
            excluded_groups[group.group_id] = "Последний платеж старше двух периодов"
            continue
        regular_groups.append(group.group_id)
        regular_ids.update(group.transaction_ids)
        events.extend(_events(group, [by_id[item] for item in group.transaction_ids], start, end))
    for rule in recurring_rules:
        if rule.enabled and rule.id not in applied_rule_ids:
            events.extend(_rule_events(rule, start, end))
    events.sort(key=lambda item: (item.date, item.group_id))
    buckets = defaultdict(list)
    regular_history_ids = sorted(item.id for item in window if item.id in regular_ids)
    for item in window:
        if item.id not in regular_ids and item.id not in managed_ids:
            buckets[(categories[item.id].category, item.direction)].append(item)
    residual_ids, excluded_history_ids = [], sorted(item.id for item in window if item.id in managed_ids and item.id not in regular_ids)
    averages = {}
    for key, items in sorted(buckets.items()):
        if history_days < MIN_HISTORY_DAYS or (as_of - history_end).days > LOOKBACK_DAYS or len(items) < MIN_BUCKET_OPERATIONS or len({item.date for item in items}) < 3:
            excluded_history_ids.extend(item.id for item in items)
            continue
        # Храним сумму и делитель отдельно, без промежуточного округления среднего.
        averages[key] = sum((item.amount for item in items), ZERO)
        residual_ids.extend(item.id for item in items)
    if excluded_history_ids:
        warnings.append("Часть остаточной истории не экстраполируется: требуется минимум 30 дней истории, давность до 90 дней и 3 операции на 3 разных датах в категории/направлении")
    if not regular_groups:
        warnings.append("Нет подтвержденных серий, пригодных для расписания")
    if excluded_groups:
        warnings.append("Неопределенные и непригодные серии не планируются; их операции могут участвовать только в историческом среднем при достаточной истории")
    warnings.append("Исторические средние не учитывают сезонность и разовые изменения; расписание предполагает продолжение серии")
    event_days = defaultdict(list)
    for event in events:
        event_days[event.date].append(event)
    category_totals = defaultdict(lambda: {"income": ZERO, "expense": ZERO})
    previous_allocations = defaultdict(lambda: ZERO)
    daily = []
    cumulative = ZERO
    for offset in range(1, HORIZON_DAYS + 1):
        day = as_of + timedelta(days=offset)
        regular = {"income": ZERO, "expense": ZERO}
        historical = {"income": ZERO, "expense": ZERO}
        for event in event_days[day]:
            regular[event.direction] += event.amount
            category_totals[event.category][event.direction] += event.amount
        for key, total in averages.items():
            category, direction = key
            # Разность округленных накопленных сумм распределяет копейки без дрейфа итога.
            target = (total * offset / Decimal(history_days)).quantize(CENT, rounding=ROUND_HALF_UP)
            amount = target - previous_allocations[key]
            previous_allocations[key] = target
            historical[direction] += amount
            category_totals[category][direction] += amount
        income = regular["income"] + historical["income"]
        expense = regular["expense"] + historical["expense"]
        net = income - expense
        cumulative += net
        daily.append(ForecastDay(date=day, income=income, expense=expense, net_cash_flow=net,
                                 cumulative_cash_flow=cumulative, balance=balance + cumulative if balance is not None else None,
                                 regular_income=regular["income"], regular_expense=regular["expense"],
                                 historical_income=historical["income"], historical_expense=historical["expense"]))
    total_income = sum((item.income for item in daily), ZERO)
    total_expense = sum((item.expense for item in daily), ZERO)
    return ForecastResult(
        as_of=as_of, start_date=start, end_date=end, history_start=history_start, history_end=history_end,
        history_transactions=len(history), history_window_transactions=len(window), history_days=history_days,
        history_lag_days=(as_of - history_end).days if history_end else None,
        opening_balance_source=balance_source, regular_group_ids=sorted(regular_groups),
        excluded_regular_groups=dict(sorted(excluded_groups.items())), regular_history_transaction_ids=regular_history_ids,
        residual_history_transaction_ids=sorted(residual_ids), excluded_history_transaction_ids=sorted(excluded_history_ids),
        scheduled_events=events, daily=daily,
        categories=[ForecastCategory(category=category, category_name=CATEGORY_LABELS.get(category, category),
                                     income=values["income"], expense=values["expense"], net_cash_flow=values["income"] - values["expense"])
                    for category, values in sorted(category_totals.items())],
        summary=ForecastSummary(total_income=total_income, total_expense=total_expense,
                                net_cash_flow=total_income - total_expense, opening_balance=balance,
                                closing_balance=balance + cumulative if balance is not None else None),
        warnings=warnings,
        balance_info=info,
    )


def forecast_cash_flow(
    transactions: Sequence[Transaction], *, as_of: date | None = None,
    opening_balance: Decimal | None = None, categorizations: Mapping[int, Categorization] | None = None,
    balance_info: StatementBalance | None = None, recurring_rules: Sequence[RecurringRule] = (),
) -> ForecastResult:
    """Без as_of прогноз от последнего дня выписки, не позже текущей даты проекта."""
    if opening_balance is not None and not opening_balance.is_finite():
        raise ValueError("Исходный остаток должен быть конечным числом")
    with localcontext() as context:
        amounts = [value for item in transactions for value in (item.amount, item.balance) if value is not None]
        if opening_balance is not None:
            amounts.append(opening_balance)
        context.prec = max(50, max((len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in amounts), default=0) + len(str(len(transactions))) + 20)
        if as_of is None:
            today = project_date()
            available_dates = [item.date for item in transactions if item.date <= today]
            calculation_date = max(available_dates) if available_dates else today
            date_source = "statement_end" if available_dates else "current_date"
        else:
            calculation_date, date_source = as_of, "explicit"
        result = _build_forecast(transactions, calculation_date, opening_balance, categorizations, balance_info, recurring_rules)
        return result.model_copy(update={"as_of_source": date_source})
