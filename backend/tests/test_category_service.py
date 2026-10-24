from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas import Transaction
from backend.services.category_rules import RULES, Rule, normalize_category_text
from backend.services.category_service import categorize_transaction, categorize_transactions
from backend.services.transaction_service import normalize_transactions


@pytest.fixture
def transaction(frame):
    return normalize_transactions(frame).transactions[0]


@pytest.mark.parametrize("direction,description,expected", [
    ("income", "Оплата по УПД № 100 от 01.10.2024. Без налога (НДС)", "customer_receipts"),
    ("income", "Доплата за техническую поддержку согласно счету №1", "customer_receipts"),
    ("income", "Инкассированная выручка по сумке № 1", "sales"),
    ("income", "Зачисление средств по операциям эквайринга. Комиссия 20, возврат покупки 0.00", "sales"),
    ("income", "Зачисление по операциям оплаты через СБП (QR-код). Комиссия 1.00", "sales"),
    ("income", "Выплата страхового возмещения по полису №1", "other_income"),
    ("expense", "Оплата по счету за канцтовары и хозтовары. НДС 20%", "supplies"),
    ("expense", "Оплата по счету за расходные материалы", "supplies"),
    ("expense", "Оплата по счету за оборудование", "supplies"),
    ("expense", "Перечисление заработной платы (аванс) по реестру", "payroll"),
    ("expense", "Аванс по заработной плате. Перечисление на счет сотрудника", "payroll"),
    ("expense", "Оплата услуг по договору ГПХ, чек № 123", "contractors"),
    ("expense", "Оплата аренды нежилого помещения", "rent"),
    ("expense", "Плата за аренду POS-терминалов", "equipment_rent"),
    ("expense", "Единый налоговый платёж", "taxes"),
    ("expense", "ЕНП", "taxes"),
    ("expense", "Комиссия за ведение счета", "bank_fees"),
    ("expense", "Пополнение рекламного кабинета по договору-оферте", "marketing"),
    ("expense", "Оплата по счету за вывоз ТКО", "utilities"),
    ("expense", "Оплата за электроэнергию по договору", "utilities"),
    ("expense", "Оплата услуг связи и доступа в Интернет", "telecom"),
    ("expense", "Оплата облачных услуг по договору-оферте", "software"),
    ("expense", "Оплата за предоставление права использования ПО (подписка)", "software"),
    ("expense", "Оплата по счету за бухгалтерские услуги", "business_services"),
    ("expense", "Оплата по счету за услуги уборки", "business_services"),
    ("expense", "Перевод собственных средств на расчетный счет в АО Банк", "own_transfers"),
    ("income", "Перевод между своими счетами", "own_transfers"),
    ("expense", "Перевод собственных средств ИП на счет физического лица", "owner_withdrawals"),
    ("expense", "Перевод собственных средств на личную карту", "owner_withdrawals"),
    ("expense", "Перевод на личный счет предпринимателя", "owner_withdrawals"),
    ("income", "Возврат оплаты аренды", "refund_in"),
    ("expense", "Возврат оплаты клиенту за оборудование", "refund_out"),
])
def test_financial_categories(transaction, direction, description, expected):
    original = transaction.model_copy(update={"direction": direction, "description": description})
    before = original.model_dump()
    result = categorize_transaction(original)
    assert result.category == expected
    assert result.status == "automatic"
    assert result.is_automatic
    assert result.source == "rules-v1"
    assert result.matched_rules
    assert original.model_dump() == before


@pytest.mark.parametrize("description", ["", "   ", "Операция по договору №42", "Без НДС", "Прочее"])
def test_unknown_does_not_become_other(transaction, description):
    # Пустые описания запрещены загрузчиком Sprint 1, но сервис устойчив к старым данным.
    item = transaction.model_copy(update={"description": description})
    before = item.model_dump()
    result = categorize_transaction(item)
    assert result.category == "unknown"
    assert result.status == "undefined"
    assert not result.is_automatic
    assert result.matched_rules == []
    assert item.model_dump() == before


def test_text_normalization(transaction):
    result = categorize_transaction(transaction.model_copy(update={
        "direction": "expense", "description": "  ЕДИНЫЙ\u00a0НАЛОГОВЫЙ!!!  ПЛАТЁЖ...  ",
    }))
    assert result.category == "taxes"
    assert normalize_category_text("ПОС—терминал") == "пос терминал"


def test_counterparty_context_is_not_enough(transaction):
    item = transaction.model_copy(update={"direction": "expense", "description": "Платеж", "counterparty": "ПАО Банк"})
    assert categorize_transaction(item).category == "unknown"
    item = item.model_copy(update={"description": "Комиссия за перевод"})
    assert categorize_transaction(item).category == "bank_fees"


def test_unknown_counterparty_with_explicit_purpose(transaction):
    item = transaction.model_copy(update={"direction": "expense", "description": "Комиссия за ведение счета", "counterparty": "Неизвестен", "inn": None})
    assert categorize_transaction(item).category == "bank_fees"


@pytest.mark.parametrize("direction,description", [
    ("income", "Комиссия за ведение счета"),
    ("income", "Единый налоговый платеж"),
    ("expense", "Зачисление средств по операциям эквайринга"),
    ("expense", "Оплата по счету №1"),
])
def test_direction_and_evidence_required(transaction, direction, description):
    result = categorize_transaction(transaction.model_copy(update={"direction": direction, "description": description}))
    assert result.category == "unknown"


def test_specific_rule_wins_independent_of_order(transaction):
    item = transaction.model_copy(update={"direction": "expense", "description": "Плата за аренду POS-терминалов"})
    result = categorize_transaction(item)
    assert result.category == "equipment_rent"
    assert result.matched_rules == ["equipment_rental"]
    assert categorize_transaction(item, tuple(reversed(RULES))) == result


def test_conflict_requires_review(transaction):
    item = transaction.model_copy(update={"direction": "expense", "description": "Оплата за электроэнергию и услуги уборки"})
    result = categorize_transaction(item)
    assert result.category == "unknown"
    assert result.status == "needs_review"
    assert not result.is_automatic
    assert result.matched_rules == ["business_services", "utilities"]
    assert categorize_transaction(item, tuple(reversed(RULES))) == result


def test_same_category_matches_are_not_a_conflict(transaction):
    rules = (Rule("a", "rent", ("income",), 10, "оплата"), Rule("b", "rent", ("income",), 10, "оплата"))
    result = categorize_transaction(transaction, rules)
    assert result.category == "rent"
    assert result.matched_rules == ["a", "b"]


def test_unspecified_transfer_requires_review(transaction):
    item = transaction.model_copy(update={"description": "Перевод за услуги по договору"})
    result = categorize_transaction(item)
    assert result.category == "unknown"
    assert result.status == "needs_review"


def test_summary_excludes_unknown(transaction):
    unknown = transaction.model_copy(update={"id": 2, "description": "Неизвестный платеж"})
    ambiguous = transaction.model_copy(update={"id": 3, "description": "Перевод"})
    result = categorize_transactions([transaction, unknown, ambiguous])
    assert result.total_transactions == 3
    assert result.categorized_transactions == 1
    assert result.undefined_transactions == 2
    assert result.needs_review_transactions == 1
    assert result.automatic_percentage == 33.33
    assert result.distribution == {"customer_receipts": 1, "unknown": 2}


def test_duplicate_ids_rejected(transaction):
    with pytest.raises(ValueError, match="уникальными"):
        categorize_transactions([transaction, transaction])


def test_zero_amount_not_permitted(transaction):
    with pytest.raises(ValidationError):
        Transaction.model_validate({**transaction.model_dump(), "amount": Decimal(0)})


def test_empty_batch():
    result = categorize_transactions([])
    assert result.total_transactions == result.automatic_percentage == 0
    assert result.categorizations == []
