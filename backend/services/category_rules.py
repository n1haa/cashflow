"""Централизованные правила v1: больший priority означает более специфичный признак."""

from dataclasses import dataclass
import re
import unicodedata

CATEGORY_LABELS = {
    "sales": "Продажи",
    "customer_receipts": "Поступления от клиентов",
    "other_income": "Прочие поступления",
    "supplies": "Закупки и поставщики",
    "payroll": "Фонд оплаты труда",
    "contractors": "Услуги по ГПХ",
    "rent": "Аренда",
    "equipment_rent": "Аренда оборудования",
    "taxes": "Налоги и сборы",
    "bank_fees": "Банковские комиссии",
    "marketing": "Маркетинг",
    "utilities": "Коммунальные услуги",
    "telecom": "Связь и интернет",
    "software": "ПО и облачные услуги",
    "business_services": "Хозяйственные и бухгалтерские услуги",
    "own_transfers": "Переводы между своими счетами",
    "owner_withdrawals": "Вывод средств предпринимателя",
    "refund_in": "Возвраты поступившие",
    "refund_out": "Возвраты выплаченные",
    "unknown": "Не определено",
}


def normalize_category_text(text: str | None) -> str:
    """Только текст поиска: исходные описания не изменяются, слова не сокращаются."""
    text = unicodedata.normalize("NFKC", text or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", text).replace("_", " ").split())


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    directions: tuple[str, ...]
    priority: int
    pattern: str
    counterparty_pattern: str | None = None

    def matches(self, description: str, counterparty: str, direction: str) -> bool:
        return (
            direction in self.directions
            and re.search(self.pattern, description) is not None
            and (self.counterparty_pattern is None or re.search(self.counterparty_pattern, counterparty) is not None)
        )


RULES_VERSION = "rules-v1"
RULES = (
    # Возврат как самостоятельная операция, а не упоминание в реестре эквайринга.
    Rule("refund_in", "refund_in", ("income",), 120, r"^(?:возврат|возвращены|возвращение)\b"),
    Rule("refund_out", "refund_out", ("expense",), 120, r"^(?:возврат|возвращены|возвращение)\b"),
    Rule("owner_withdrawal", "owner_withdrawals", ("expense",), 110,
         r"^перевод\b.*(?:на личн\w* (?:карту|счет)|на счет физического лица)"),
    Rule("own_transfer", "own_transfers", ("income", "expense"), 100,
         r"^перевод\b.*(?:собственных средств|между своими счетами)"),
    Rule("unresolved_transfer", "unknown", ("income", "expense"), 90, r"^перевод\b"),
    Rule("bank_account_fee", "bank_fees", ("expense",), 80,
         r"\bкомиссия за (?:ведение|обслуживание) счета\b"),
    Rule("bank_fee_context", "bank_fees", ("expense",), 80, r"^комисси\w*\b", r"\bбанк\w*\b"),
    Rule("equipment_rental", "equipment_rent", ("expense",), 70,
         r"\bаренд\w*\s+(?:pos терминал\w*|оборудовани\w*)\b"),
    Rule("tax_payment", "taxes", ("expense",), 60,
         r"\b(?:единый налоговый платеж|енп|уплата налог\w*|уплата сбор\w*|страховые взносы)\b"),
    Rule("payroll", "payroll", ("expense",), 60, r"\bзаработн\w* плат\w*\b"),
    Rule("civil_contract", "contractors", ("expense",), 60, r"\bдоговор\w* гпх\b"),
    Rule("advertising", "marketing", ("expense",), 60, r"\b(?:рекламн\w* кабинет\w*|оплата реклам\w*)\b"),
    Rule("retail_sales", "sales", ("income",), 60,
         r"^(?:зачисление (?:средств по операциям эквайринга|по операциям оплаты через сбп)|инкассированная выручка)\b"),
    Rule("insurance_payout", "other_income", ("income",), 60, r"^выплата страхового возмещения\b"),
    Rule("premises_rental", "rent", ("expense",), 50, r"\bаренд\w*\b"),
    Rule("utilities", "utilities", ("expense",), 50,
         r"\b(?:вывоз тко|электроэнерги\w*|водоснабжени\w*|водоотведени\w*)\b"),
    Rule("telecom", "telecom", ("expense",), 50, r"\b(?:услуг\w* связи|доступ\w* в интернет)\b"),
    Rule("software", "software", ("expense",), 50,
         r"\b(?:облачн\w* услуг\w*|права использования по|подписка на по)\b"),
    Rule("business_services", "business_services", ("expense",), 50,
         r"\b(?:бухгалтерск\w* услуг\w*|услуг\w* уборки)\b"),
    Rule("supplies", "supplies", ("expense",), 50,
         r"\bза (?:канцтовары|хозтовары|расходные материалы|оборудование)\b"),
    Rule("customer_payment", "customer_receipts", ("income",), 10,
         r"^(?:(?:частичная )?оплата|доплата)\b.*\b(?:по (?:счету|договору|упд)|за (?:доработку|разработку|техническую поддержку|абонентское обслуживание|услуги)|услуг\w*)\b"),
)
