"""Воспроизводимый отчет по двум внешним CSV, без копирования исходных платежей.

python -m scripts.analyze_categorization --dataset-dir PATH --output docs/sprint2_results.json
"""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from backend.services.category_rules import CATEGORY_LABELS, RULES_VERSION
from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.transaction_service import normalize_transactions


def analyze_file(path: Path) -> dict:
    content = path.read_bytes()
    imported = normalize_transactions(load_transactions_file(content, path.name))
    result = categorize_transactions(imported.transactions)
    groups = defaultdict(list)
    for category, source in zip(result.categorizations, imported.source_rows):
        groups[category.category].append({"operation_id": source.operation_id, "source_row": source.source_row})
    return {
        "file": path.name, "sha256": hashlib.sha256(content).hexdigest(),
        "import_status": imported.status, "missing_inn": imported.invalid_transactions,
        **result.model_dump(exclude={"categorizations"}),
        "review_references": {
            category: [rows[index] for index in sorted({0, len(rows) - 1})]
            for category, rows in sorted(groups.items())
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "rules_version": RULES_VERSION, "category_labels": CATEGORY_LABELS,
        "note": "Доля автоматического назначения категорий не является оценкой точности",
        "datasets": [analyze_file(args.dataset_dir / filename) for filename in ("C071.csv", "C015.csv")],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
