"""Анализ кассовых разрывов на готовом прогнозе Sprint 4 по обоим оригиналам."""

import argparse
from datetime import date
import json
from pathlib import Path

from backend.schemas import ForecastResult
from backend.services.cash_gap_service import analyze_cash_gaps
from backend.services.forecast_service import project_date
from scripts.analyze_forecast import analyze_file


def analyze_snapshot(path: Path, as_of: date) -> dict:
    source = analyze_file(path, as_of)  # Один вызов существующего прогноза для этого среза.
    forecast = ForecastResult.model_validate(source["forecast"])
    return {"as_of": str(as_of), "source_sha256": source["sha256"],
            "source_history_start": source["source_history_start"], "source_history_end": source["source_history_end"],
            "history_transactions": forecast.history_transactions,
            "opening_balance_source": forecast.opening_balance_source,
            "analysis": analyze_cash_gaps(forecast).model_dump(mode="json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--reference-as-of", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    datasets = []
    for name in ("C071.csv", "C015.csv"):
        path = args.dataset_dir / name
        record = {"file": name, "current": analyze_snapshot(path, args.as_of or project_date())}
        if args.reference_as_of is not None:
            record["reference"] = analyze_snapshot(path, args.reference_as_of)
        datasets.append(record)
    report = {"note": "Анализ готового прогноза; неизвестный баланс не означает отсутствие разрывов", "datasets": datasets}
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
