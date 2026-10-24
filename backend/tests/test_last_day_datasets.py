from decimal import Decimal
import hashlib
from pathlib import Path

import pytest


@pytest.mark.parametrize("filename,count", [("C018.csv", 2), ("C052.csv", 1)])
def test_reported_last_day_on_datasets(request, client, filename, count):
    directory = request.config.getoption('--dataset-dir')
    if directory is None:
        pytest.skip('Укажите каталог с C018.csv и C052.csv через --dataset-dir')
    path = Path(directory) / filename
    # Основные старые проверки используют отдельный каталог C071/C015.
    if not path.is_file():
        pytest.skip('В указанном каталоге нет дополнительного датасета')
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    uploaded = client.post('/api/transactions/upload', files={'file': (filename, content)})
    assert uploaded.status_code == 200
    body = uploaded.json()
    info = body['balance_info']
    assert info['status'] == 'invalid'  # Исторические несоответствия не скрываются.
    assert len(info['issues']) == count
    last_date = max(row['date'] for row in body['transactions'])
    balances = {Decimal(row['balance']) for row in body['transactions'] if row['date'] == last_date}
    assert len(balances) == 1
    expected = next(iter(balances))
    forecast = client.get('/api/forecast').json()
    assert forecast['as_of'] == last_date
    assert Decimal(forecast['summary']['opening_balance']) == expected
    assert Decimal(info['statement_closing_balance']) == expected
    assert forecast['opening_balance_source'] == 'statement_reported'
    assert forecast['warnings']
    for day in forecast['daily']:
        assert Decimal(day['balance']) == expected + Decimal(day['cumulative_cash_flow'])
    analysis = client.get('/api/forecast/cash-gaps').json()
    assert analysis['status'] != 'not_available'
    client.post('/api/transactions/upload', files={'file': (filename, content)})
    assert client.get('/api/forecast').json() == forecast
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
