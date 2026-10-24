"""Повторяемость по всей последовательности дат, без прогнозирования денежного потока."""

from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import re
from statistics import median_low
import unicodedata

from backend.schemas import Categorization, RecurringOperation, RecurringResult, Transaction
from backend.services.category_rules import CATEGORY_LABELS, normalize_category_text
from backend.services.category_service import categorize_transaction

# Отклонения в днях — фиксированные правила MVP, не оценка вероятности.
DAY_PERIODS = (("weekly", 7, 2), ("biweekly", 14, 2))
MONTH_PERIODS = (("monthly", 1, 5), ("quarterly", 3, 7), ("yearly", 12, 10))
MONTH_NAMES = r"(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])"


def normalize_recurring_description(description: str) -> str:
    """Удаляем изменяемые реквизиты, сохраняя договоры, объекты и этапы платежей."""
    # NFKC превращает символ № в латинское No; заменяем его до нормализации.
    text = unicodedata.normalize("NFKC", description.replace("№", "#")).casefold().replace("ё", "е")
    text = " ".join(text.split())
    # Финансовые детали реестра и НДС не являются предметом платежа.
    text = re.split(r"(?:\.\s*(?:сумма\b|без\b|ндс\b|в т\b|в том\b)|\bсумма\s+\d)", text)[0]
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[./]\d{1,2}[./]\d{4}(?:\s*г\.?)?", " ", text)
    text = re.sub(rf"\bза\s+{MONTH_NAMES}\s+\d{{4}}(?:\s*г\.?)?", " ", text)
    text = re.sub(r"\b(?:согласно\s+)?(?:по\s+)?(?:счету|счет|упд|реестру|реестр|чек|сумке)\s*#\s*[\w/-]+(?:\s+от)?", " ", text)
    text = re.sub(r"\b(?:от|за)\s*(?=[.,;]|$)", " ", text)
    text = re.sub(r"\b(?:единый налоговый платеж|енп)\b", "единый налоговый платеж", text)
    return normalize_category_text(text)


def _calendar_date(origin: date, months: int, anchor_day: int, end_of_month: bool) -> date:
    month_number = origin.year * 12 + origin.month - 1 + months
    year, month_zero = divmod(month_number, 12)
    month = month_zero + 1
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day if end_of_month else min(anchor_day, last_day))


def _within_exception_limit(errors: int, observations: int) -> bool:
    # Не более 15% исключений; на двух-трех операциях исключения не допускаются.
    return errors * 100 <= observations * 15


def _period(dates: list[date]) -> tuple[str, date | None, int, int, bool]:
    """Период, следующая дата, исключения дат/интервалов, нестабильный день месяца."""
    if len(dates) < 2:
        return "unknown", None, 0, 0, False
    intervals = [(right - left).days for left, right in zip(dates, dates[1:])]
    for name, days, tolerance in DAY_PERIODS:
        if any(interval > days * 3 + tolerance for interval in intervals):
            continue
        interval_errors = sum(abs(interval - days) > tolerance for interval in intervals)
        # Фаза относительно всей серии защищает от накопленного дрейфа и кратных частот.
        phases = [(actual - dates[0]).days % days for actual in dates]
        phase_counts = Counter(phases)
        anchor_phase = min(phase_counts, key=lambda value: (-phase_counts[value], value))
        date_errors = sum(min((phase - anchor_phase) % days, (anchor_phase - phase) % days) > tolerance for phase in phases)
        if _within_exception_limit(interval_errors, len(intervals)) and _within_exception_limit(date_errors, len(dates)):
            try:
                return name, dates[-1] + timedelta(days=days), date_errors, interval_errors, False
            except OverflowError:
                return name, None, date_errors, interval_errors, False
    anchor_day = median_low(sorted(item.day for item in dates))
    end_of_month = all(item.day == monthrange(item.year, item.month)[1] for item in dates)
    for name, months, tolerance in MONTH_PERIODS:
        expected = []
        for actual in dates:
            candidates = []
            for offset in (-1, 0, 1):
                try:
                    candidates.append(_calendar_date(actual, offset, anchor_day, end_of_month))
                except ValueError:
                    continue
            expected.append(min(candidates, key=lambda target: (abs((actual - target).days), target)))
        month_numbers = [item.year * 12 + item.month for item in expected]
        # Две операции одного расчетного цикла — неоднозначность, не доказательство регулярности.
        if len(set(month_numbers)) != len(month_numbers):
            continue
        if any(right - left > months * 3 for left, right in zip(month_numbers, month_numbers[1:])):
            continue
        interval_errors = sum(right - left != months for left, right in zip(month_numbers, month_numbers[1:]))
        date_errors = sum(abs((actual - target).days) > tolerance for actual, target in zip(dates, expected))
        if _within_exception_limit(interval_errors, len(intervals)) and _within_exception_limit(date_errors, len(dates)):
            try:
                return name, _calendar_date(expected[-1], months, anchor_day, end_of_month), date_errors, interval_errors, False
            except ValueError:
                return name, None, date_errors, interval_errors, False
    # Месячная регулярность не требует фиксированного дня. Проверяем реальные
    # месяцы без привязки к ближайшему типичному дню: строго одна дата в каждом
    # последовательном месяце, без пропусков и дополнительных дат внутри месяца.
    month_numbers = [item.year * 12 + item.month for item in dates]
    if all(right - left == 1 for left, right in zip(month_numbers, month_numbers[1:])):
        try:
            next_date = _calendar_date(dates[-1], 1, anchor_day, end_of_month)
        except ValueError:
            next_date = None
        return "monthly", next_date, 0, 0, True
    return "unknown", None, 0, 0, False


def _make_group(key: tuple, transactions: list[Transaction]) -> RecurringOperation:
    items = sorted(transactions, key=lambda item: (item.date, item.id))
    dates = [item.date for item in items]
    amounts = [item.amount for item in items]
    unique_dates = sorted(set(dates))
    frequency, next_date, date_errors, interval_errors, date_variable = _period(unique_dates)
    same_day = len(unique_dates) != len(dates)
    status = "uncertain" if frequency == "unknown" else "confirmed" if len(unique_dates) >= 3 and not same_day else "potential"
    # Суммы не ограничивают группировку: сохраняем разброс и осторожность использования.
    minimum, maximum = min(amounts), max(amounts)
    with localcontext() as context:
        context.prec = max(28, max(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in amounts) + len(str(len(items))) + 10)
        variable = maximum > minimum * Decimal("1.5")
        average = (sum(amounts, Decimal(0)) / Decimal(len(items))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        interval = (Decimal((dates[-1] - dates[0]).days) / Decimal(len(items) - 1)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    reasons = {
        "uncertain": "Последовательность не соответствует поддерживаемому периоду либо есть несколько операций одной даты",
        "potential": "Только две операции; периодичность предварительная",
        "confirmed": "Не менее трех операций; вся последовательность соответствует периоду в допустимых пределах",
    }
    reason = reasons[status]
    if date_variable:
        reason = (
            "По одной дате платежа в каждом последовательном календарном месяце; "
            "день месяца нестабилен, следующая дата ориентировочная"
        )
        if status == "potential":
            reason += "; только две операции, периодичность предварительная"
    if status == "potential" and same_day:
        reason = "Есть ритм по различным датам, но несколько операций одной даты; требуется проверка объединения"
        if date_variable:
            reason += "; день месяца нестабилен, следующая дата ориентировочная"
    if date_errors or interval_errors:
        reason += f"; исключений по датам: {date_errors}, по интервалам: {interval_errors} (не более 15% каждого вида)"
    if variable:
        reason += "; сумма меняется более чем в 1.5 раза, среднее требует проверки"
    if frequency != "unknown" and next_date is None:
        reason += "; следующая дата выходит за поддерживаемый календарь"
    return RecurringOperation(
        group_id=hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:20],
        description=key[-1], counterparty=items[0].counterparty,
        category=key[-2], category_name=CATEGORY_LABELS.get(key[-2], key[-2]),
        direction=items[0].direction, frequency=frequency, status=status,
        transaction_ids=[item.id for item in items], repetitions=len(items), distinct_dates=len(set(dates)),
        first_date=dates[0], last_date=dates[-1], next_expected_date=next_date,
        average_amount=average, minimum_amount=minimum, maximum_amount=maximum,
        average_interval_days=interval, amount_variable=variable,
        date_exceptions=date_errors, interval_exceptions=interval_errors,
        date_variable=date_variable,
        usable_for_calculations=status == "confirmed" and not variable and not date_variable and next_date is not None,
        reason=reason,
    )


def detect_recurring_transactions(
    transactions: Sequence[Transaction],
    categorizations: Mapping[int, Categorization] | None = None,
) -> RecurringResult:
    """Возвращает повторные кандидаты всех статусов; одиночные операции — отдельными ID."""
    ids = [item.id for item in transactions]
    if len(ids) != len(set(ids)):
        raise ValueError("Идентификаторы операций должны быть уникальными")
    buckets = defaultdict(list)
    for item in transactions:
        category = categorizations.get(item.id) if categorizations is not None else None
        if category is None:
            category = categorize_transaction(item)
        description = normalize_recurring_description(item.description)
        if description in {"", "unknown", "оплата", "платеж", "перевод", "оплата по счету", "частичная оплата", "доплата"}:
            description = f"неоднозначное назначение операции {item.id}"
        key = (normalize_category_text(item.counterparty), item.inn or "", item.direction, category.category, description)
        buckets[key].append(item)
    groups = [_make_group(key, items) for key, items in sorted(buckets.items()) if len(items) >= 2]
    singles = sorted(items[0].id for items in buckets.values() if len(items) == 1)
    statuses = Counter(group.status for group in groups)
    return RecurringResult(
        total_transactions=len(transactions), total_groups=len(groups),
        confirmed_groups=statuses["confirmed"], potential_groups=statuses["potential"], uncertain_groups=statuses["uncertain"],
        frequency_distribution=dict(sorted(Counter(group.frequency for group in groups).items())),
        ungrouped_transaction_ids=singles, groups=groups,
    )
