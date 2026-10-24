"""Детерминированная категоризация существующих Transaction без изменения платежей."""

from collections import Counter
from collections.abc import Sequence

from backend.schemas import Categorization, CategorizationResult, Transaction
from backend.services.category_rules import CATEGORY_LABELS, RULES, RULES_VERSION, Rule, normalize_category_text


def categorize_transaction(transaction: Transaction, rules: Sequence[Rule] = RULES) -> Categorization:
    description = normalize_category_text(transaction.description)
    counterparty = normalize_category_text(transaction.counterparty)
    matches = [rule for rule in rules if rule.matches(description, counterparty, transaction.direction)]
    base = {"transaction_id": transaction.id, "source": RULES_VERSION}
    if not matches:
        return Categorization(**base, reason="Нет подходящего правила")
    priority = max(rule.priority for rule in matches)
    winners = [rule for rule in matches if rule.priority == priority]
    categories = {rule.category for rule in winners}
    rule_ids = sorted(rule.id for rule in winners)
    if len(categories) != 1:
        return Categorization(**base, status="needs_review", matched_rules=rule_ids,
                              reason="Конфликт правил одинакового приоритета")
    category = next(iter(categories))
    if category == "unknown":
        return Categorization(**base, status="needs_review", matched_rules=rule_ids,
                              reason="Недостаточно данных о назначении перевода")
    return Categorization(
        **base, category=category, category_name=CATEGORY_LABELS[category], status="automatic",
        is_automatic=True, matched_rules=rule_ids, reason="Совпадение правила по направлению и тексту",
    )


def categorize_transactions(transactions: Sequence[Transaction]) -> CategorizationResult:
    ids = [item.id for item in transactions]
    if len(set(ids)) != len(ids):
        raise ValueError("Идентификаторы операций должны быть уникальными")
    results = [categorize_transaction(item) for item in transactions]
    counts = Counter(item.category for item in results)
    automatic = sum(item.is_automatic for item in results)
    return CategorizationResult(
        total_transactions=len(results), categorized_transactions=automatic,
        undefined_transactions=len(results) - automatic,
        needs_review_transactions=sum(item.status == "needs_review" for item in results),
        automatic_percentage=round(automatic / len(results) * 100, 2) if results else 0,
        distribution=dict(sorted(counts.items())), categorizations=results,
    )
