"""Определение и сверка остатков выписки с учетом смысла ее столбцов."""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal, localcontext

from backend.schemas import StatementBalance, Transaction


def last_day_balance(transactions: Sequence[Transaction], kind: str = "end_of_day") -> Decimal | None:
    """Фактический остаток последнего дня, независимо от сверки предыдущей истории."""
    if not transactions:
        return None
    last_date = max(item.date for item in transactions)
    last_items = [item for item in transactions if item.date == last_date]
    if kind == "after_transaction" and len(last_items) != 1:
        return None
    values = {item.balance for item in last_items if item.balance is not None and item.balance.is_finite()}
    return next(iter(values)) if len(values) == 1 else None


def resolve_statement_balance(
    transactions: Sequence[Transaction], *, kind: str = "end_of_day",
    explicit_opening: Decimal | None = None,
) -> StatementBalance:
    items = sorted(transactions, key=lambda item: item.date)
    base = dict(kind=kind, explicit_opening_balance=explicit_opening,
                statement_start=items[0].date if items else None, statement_end=items[-1].date if items else None)
    def unavailable(status, message, issues=None):
        reported = last_day_balance(items, kind)
        if reported is not None:
            message += "; для прогноза доступен остаток последнего дня из выписки"
        return StatementBalance(**base, status=status, source="statement_reported" if reported is not None else "unavailable",
                                message=message, issues=issues or [], statement_closing_balance=reported)
    if not items:
        return unavailable("unknown", "Нет операций для определения остатка")
    values = [value for item in items for value in (item.amount, item.balance) if value is not None]
    if explicit_opening is not None:
        values.append(explicit_opening)
    if any(not value.is_finite() for value in values):
        return unavailable("invalid", "Некорректные денежные значения выписки")
    with localcontext() as context:
        context.prec = max(50, max(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in values) + len(str(len(items))) + 20)
        net = lambda item: item.amount if item.direction == "income" else -item.amount
        if kind == "after_transaction":
            if len({item.date for item in items}) != len(items):
                return unavailable("unknown", "У операций одной даты нет достоверной очередности; остаток после операции не подтвержден")
            if explicit_opening is None and items[0].balance is None:
                return unavailable("unknown", "Нет начального остатка или баланса первой операции")
            opening = explicit_opening if explicit_opening is not None else items[0].balance - net(items[0])
            running = opening
            issues = []
            for item in items:
                running += net(item)
                if item.balance is not None and item.balance != running:
                    issues.append(f"Не согласован баланс после операции ID {item.id} за {item.date.isoformat()}: рассчитано {running}, в выписке {item.balance}, разница {item.balance - running}")
                if item.balance is not None:
                    running = item.balance
                if item.balance is None and explicit_opening is None:
                    return unavailable("unknown", "Не у всех операций известен баланс после операции")
        else:
            days = defaultdict(list)
            for item in items:
                days[item.date].append(item)
            conflicting_days = [day for day, operations in sorted(days.items())
                                if len({item.balance for item in operations if item.balance is not None}) > 1]
            if conflicting_days:
                return unavailable("invalid", "В выписке разные остатки на конец одного дня",
                                   [f"Разные остатки на конец дня {day.isoformat()}" for day in conflicting_days])
            issues = []
            opening = explicit_opening
            running = opening
            for index, (day, operations) in enumerate(sorted(days.items())):
                day_net = sum((net(item) for item in operations), Decimal(0))
                balances = {item.balance for item in operations if item.balance is not None}
                closing = next(iter(balances)) if balances else None
                if index == 0 and opening is None:
                    if closing is None:
                        return unavailable("unknown", "В выписке нет начального остатка и остатка на конец первого дня")
                    opening = closing - day_net
                    running = opening
                if running is None:
                    continue
                running += day_net
                if closing is not None and running != closing:
                    issues.append(f"Остаток на конец {day.isoformat()} не согласован с операциями: рассчитано {running}, в выписке {closing}, разница {closing - running}")
                if closing is not None:
                    running = closing
                if closing is None and explicit_opening is None:
                    return unavailable("unknown", "Не для каждого дня известен остаток; сверка выписки неполна")
        if issues:
            return unavailable("invalid", "Выписка содержит несогласованные остатки; автоматический баланс не подтвержден", issues)
        return StatementBalance(
            **base, status="provided" if explicit_opening is not None else "detected",
            source="statement_opening" if explicit_opening is not None else "balance_after" if kind == "after_transaction" else "balance_eod",
            message="Остатки выписки определены и согласованы с операциями; полнота выписки предполагается",
            statement_opening_balance=opening, statement_closing_balance=running,
        )
