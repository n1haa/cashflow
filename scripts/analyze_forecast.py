"""Прогноз по обоим полным оригиналам: запуск из корня python -m scripts.analyze_forecast."""

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

from backend.services.category_service import categorize_transactions
from backend.services.file_service import load_transactions_file
from backend.services.forecast_service import forecast_cash_flow
from backend.services.transaction_service import normalize_transactions


def analyze_file(path: Path, as_of: date | None) -> dict:
    content = path.read_bytes()
    imported = normalize_transactions(load_transactions_file(content, path.name))
    categories = categorize_transactions(imported.transactions)
    result = forecast_cash_flow(imported.transactions, as_of=as_of,
                                categorizations={item.transaction_id: item for item in categories.categorizations}, balance_info=imported.balance_info)
    return {
        "file": path.name, "sha256": hashlib.sha256(content).hexdigest(),
        "source_history_start": str(min(item.date for item in imported.transactions)),
        "source_history_end": str(max(item.date for item in imported.transactions)),
        "import_status": imported.status, "missing_inn": imported.invalid_transactions,
        "forecast": result.model_dump(mode="json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"note": "Детерминированный сценарий по истории, точность на будущих данных не измерена",
              "datasets": [analyze_file(args.dataset_dir / filename, args.as_of)
                           for filename in ("C071.csv", "C015.csv")]}
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
