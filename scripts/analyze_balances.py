"""Проверка автоматического остатка и существующего прогноза на обоих оригиналах."""

import argparse
import json
from pathlib import Path

from backend.schemas import ForecastResult
from backend.services.cash_gap_service import analyze_cash_gaps
from scripts.analyze_forecast import analyze_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = []
    for name in ('C071.csv', 'C015.csv'):
        source = analyze_file(args.dataset_dir / name, None)
        forecast = ForecastResult.model_validate(source['forecast'])
        gaps = analyze_cash_gaps(forecast)
        results.append({'file': name, 'sha256': source['sha256'], 'transactions': forecast.history_transactions,
                        'balance_info': forecast.balance_info.model_dump(mode='json'),
                        'as_of': str(forecast.as_of), 'as_of_source': forecast.as_of_source,
                        'start_date': str(forecast.start_date), 'end_date': str(forecast.end_date),
                        'summary': forecast.summary.model_dump(mode='json'),
                        'first_forecast_day': forecast.daily[0].model_dump(mode='json'),
                        'cash_gaps': gaps.model_dump(mode='json', exclude={'daily', 'warnings'})})
    text = json.dumps({'datasets': results}, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
