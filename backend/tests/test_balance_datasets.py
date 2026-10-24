from decimal import Decimal
import hashlib
from pathlib import Path

import pytest


@pytest.mark.parametrize('filename,opening,closing,ending,minimum', [
    ('C071.csv', '6484608.48', '3756563.37', '3072379.65', '2022734.55'),
    ('C015.csv', '400854.05', '518152.13', '506614.54', '429517.41'),
])
def test_automatic_end_to_end_on_original(request, client, filename, opening, closing, ending, minimum):
    directory = request.config.getoption('--dataset-dir')
    if directory is None:
        pytest.skip('Для проверки оригиналов укажите --dataset-dir')
    path = Path(directory) / filename
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    response = client.post('/api/transactions/upload', files={'file': (filename, content)})
    assert response.status_code == 200
    info = response.json()['balance_info']
    assert info['status'] == 'detected'
    assert info['kind'] == 'end_of_day'
    assert Decimal(info['statement_opening_balance']) == Decimal(opening)
    assert Decimal(info['statement_closing_balance']) == Decimal(closing)
    forecast = client.get('/api/forecast').json()
    assert forecast['as_of'] == '2026-09-30'
    assert forecast['as_of_source'] == 'statement_end'
    assert forecast['start_date'] == '2026-10-01'
    assert forecast['end_date'] == '2026-10-30'
    assert Decimal(forecast['summary']['opening_balance']) == Decimal(closing)
    assert Decimal(forecast['summary']['closing_balance']) == Decimal(ending)
    for day in forecast['daily']:
        assert Decimal(day['balance']) == Decimal(closing) + Decimal(day['cumulative_cash_flow'])
    gaps = client.get('/api/forecast/cash-gaps').json()
    assert gaps['status'] == 'no_gaps'
    assert Decimal(gaps['minimum_balance']) == Decimal(minimum)
    assert gaps['gap_count'] == 0
    client.post('/api/transactions/upload', files={'file': (filename, content)})
    assert client.get('/api/forecast').json() == forecast
    assert client.get('/api/forecast/cash-gaps').json() == gaps
    assert client.get('/api/forecast', params={'as_of': '2026-10-24'}).json()['summary']['opening_balance'] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
