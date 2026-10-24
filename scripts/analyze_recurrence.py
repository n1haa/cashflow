"""Отчет регулярности по полным внешним CSV; финансовые источники не изменяются."""

import argparse
import hashlib
import json
from pathlib import Path

from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.recurring_service import detect_recurring_transactions
from backend.services.transaction_service import normalize_transactions


def analyze_file(path: Path) -> dict:
    content = path.read_bytes()
    imported = normalize_transactions(load_transactions_file(content, path.name))
    categories = categorize_transactions(imported.transactions)
    result = detect_recurring_transactions(imported.transactions, {item.transaction_id: item for item in categories.categorizations})
    sources = {item.transaction_id: item for item in imported.source_rows}
    # Без имен физических лиц, ИНН и полного назначения; ID позволяют свериться с оригиналом.
    groups = []
    for group in result.groups:
        record = group.model_dump(mode="json", exclude={"counterparty", "description"})
        record["source_references"] = [sources[item].model_dump() for item in (group.transaction_ids[0], group.transaction_ids[-1])]
        groups.append(record)
    return {
        "file": path.name, "sha256": hashlib.sha256(content).hexdigest(),
        "import_status": imported.status, "missing_inn": imported.invalid_transactions,
        **result.model_dump(mode="json", exclude={"groups"}),
        "scheduled_groups": result.confirmed_groups + result.potential_groups,
        "usable_groups": sum(group.usable_for_calculations for group in result.groups),
        "groups": groups,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "note": "Результаты правил регулярности, не прогноз денежного потока и не измеренная точность",
        "datasets": [analyze_file(args.dataset_dir / filename) for filename in ("C071.csv", "C015.csv")],
    }
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
