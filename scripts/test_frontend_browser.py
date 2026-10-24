"""Реальный browser E2E: python -m scripts.test_frontend_browser --dataset-dir PATH."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', required=True, type=Path)
    parser.add_argument('--browser-channel', default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
                               cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    results = []
    datasets = []
    try:
        for _ in range(100):
            try:
                with urlopen(base, timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Не запустился локальный сервер')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.browser_channel, headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            expect(page.locator('#transactions-content')).to_contain_text('пока нет операций')
            expect(page.locator('#metric-opening')).to_have_text('Нет данных')
            results.append('initial-empty-state')
            for filename, total, missing in [('C071.csv', 614, 5), ('C015.csv', 2455, 96)]:
                path = args.dataset_dir / filename
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                page.locator('#statement-file').set_input_files(str(path))
                expect(page.locator('#file-info')).to_contain_text(filename)
                page.locator('#upload-submit').click()
                expect(page.locator('#upload-message')).to_contain_text('Обработка завершена', timeout=60000)
                expect(page.locator('#metric-transactions')).to_have_text(str(total))
                expect(page.locator('#import-result')).to_contain_text(f'с ошибками данных: {missing}')
                expect(page.locator('#transactions-content tbody tr')).to_have_count(25)
                expect(page.locator('#transactions-content')).to_contain_text('Автоматически')
                page.locator('#transactions-content button[data-step="1"]').click()
                expect(page.locator('#transactions-content')).to_contain_text('Страница 2 из')
                expect(page.locator('#transactions-content')).to_contain_text(f'{path.stem}-000026')
                page.locator('#transactions-content button[data-step="-1"]').click()
                expect(page.locator('#recurring-content')).to_contain_text('Подтверждено:')
                expect(page.locator('#forecast-content svg')).to_have_count(1)
                expect(page.locator('#forecast-content')).to_contain_text('Прогнозируемый остаток')
                expect(page.locator('#gaps-content')).to_contain_text('Кассовые разрывы не обнаружены')
                expect(page.locator('#import-result')).to_contain_text('Остаток определен автоматически')
                expect(page.locator('#metric-opening')).not_to_have_text('Нет данных')
                original_opening = page.locator('#metric-opening').inner_text()
                original_closing = page.locator('#metric-closing').inner_text()
                # Повторный импорт не добавляет ни баланс, ни операции второй раз.
                page.locator('#upload-submit').click()
                expect(page.locator('#upload-message')).to_contain_text('Обработка завершена', timeout=60000)
                expect(page.locator('#metric-opening')).to_have_text(original_opening)
                expect(page.locator('#metric-closing')).to_have_text(original_closing)
                expect(page.locator('#metric-transactions')).to_have_text(str(total))
                page.locator('#as-of').fill('2026-10-24')
                page.locator('#refresh-all').click()
                expect(page.locator('#settings-message')).to_contain_text('Запросы завершены', timeout=60000)
                expect(page.locator('#gaps-content')).to_contain_text('Анализ недоступен')
                expect(page.locator('#metric-opening')).to_have_text('Нет данных')
                page.locator('#transaction-direction').select_option('expense')
                expect(page.locator('#transactions-content')).not_to_contain_text('Поступление')
                page.locator('#transaction-direction').select_option('all')
                page.locator('#transaction-search').fill('невстречающийся текст запроса')
                expect(page.locator('#transactions-content')).to_contain_text('Нет операций по выбранному фильтру')
                page.locator('#transaction-search').fill('')
                page.locator('#as-of').fill('2026-09-30')
                page.locator('#refresh-all').click()
                expect(page.locator('#settings-message')).to_contain_text('Запросы завершены', timeout=60000)
                expect(page.locator('#gaps-content')).to_contain_text('Кассовые разрывы не обнаружены')
                expect(page.locator('#forecast-content')).to_contain_text('Прогнозируемый остаток')
                expect(page.locator('#metric-opening')).not_to_have_text('Нет данных')
                expect(page.locator('#forecast-content table').first.locator('tbody tr')).to_have_count(30)
                datasets.append({'file': filename, 'sha256': checksum,
                                 'displayed_transactions': int(page.locator('#metric-transactions').inner_text()),
                                 'missing_inn_errors': missing, 'daily_rows': 30,
                                 'automatic_gap_state': 'no_gaps', 'current_gap_state': 'not_available',
                                 'historical_gap_state': 'no_gaps', 'automatic_opening': original_opening,
                                 'automatic_closing': original_closing, 'repeat_import': 'unchanged'})
                assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
                results.append(f'{filename}: upload, categories, recurrence, forecast, unavailable/known balance, filters')
                if filename == 'C071.csv' and args.screenshot:
                    page.locator('a[href="#overview"]').first.click()
                    page.screenshot(path=str(args.screenshot), full_page=False)
                page.locator('#as-of').fill('')
            # Ошибка нового импорта сохраняет предыдущие результаты.
            page.locator('#statement-file').set_input_files({'name': 'broken.csv', 'mimeType': 'text/csv', 'buffer': b'a;b\n1;2'})
            page.locator('#upload-submit').click()
            expect(page.locator('#upload-message')).to_contain_text('предыдущая успешно загруженная', timeout=60000)
            expect(page.locator('#metric-transactions')).to_have_text('2455')
            results.append('invalid-upload-preserves-results')
            # Реальные остальные модули продолжают работать при отказе одного endpoint.
            page.route('**/api/transactions/recurring', lambda route: route.fulfill(status=503, content_type='application/json', body='{"detail":"Модуль временно недоступен"}'))
            page.reload()
            expect(page.locator('#recurring-content')).to_contain_text('Модуль временно недоступен', timeout=60000)
            expect(page.locator('#forecast-content svg')).to_have_count(1, timeout=60000)
            expect(page.locator('#refresh-all')).to_be_enabled(timeout=60000)
            page.unroute('**/api/transactions/recurring')
            page.locator('#recurring-content button[data-action="retry"]').click()
            expect(page.locator('#recurring-content')).to_contain_text('Подтверждено:', timeout=60000)
            results.append('module-error-isolation-and-retry')
            expect(page.locator('#refresh-all')).to_be_enabled(timeout=60000)
            page.route('**/api/forecast', lambda route: route.fulfill(content_type='application/json', body='{"daily":null}'))
            page.reload()
            expect(page.locator('#forecast-content')).to_contain_text('неполон', timeout=60000)
            expect(page.locator('#recurring-content')).to_contain_text('Подтверждено:', timeout=60000)
            results.append('malformed-module-response')
            page.unroute('**/api/forecast')
            # Неполный, но отображаемый ответ: никакие отсутствующие суммы не подставляются.
            page.route('**/api/forecast', lambda route: route.fulfill(content_type='application/json', body='{"daily":[],"summary":{},"start_date":"2026-10-25"}'))
            page.reload()
            expect(page.locator('#forecast-content')).to_contain_text('неполный набор', timeout=60000)
            expect(page.locator('#metric-income')).to_have_text('Нет данных')
            results.append('incomplete-forecast-state')
            page.unroute('**/api/forecast')
            # Недоверенный текст выписки отображается как текст, не исполняемый HTML.
            unsafe = page.request.get(base + '/api/transactions').json()[0]
            unsafe['description'] = '<img src=x onerror="window.__statementInjected=true">'
            page.route('**/api/transactions', lambda route: route.fulfill(content_type='application/json', body=json.dumps([unsafe])))
            page.reload()
            expect(page.locator('#transactions-content')).to_contain_text('<img src=x', timeout=60000)
            expect(page.locator('#transactions-content img')).to_have_count(0)
            assert page.evaluate('typeof window.__statementInjected === "undefined"')
            results.append('untrusted-statement-text-escaped')
            page.unroute('**/api/transactions')
            # Только UI-fixture: реальные оригиналы не содержат обнаруженных разрывов.
            gap_fixture = {'status': 'detected', 'gap_count': 2, 'minimum_balance': '-50',
                           'maximum_deficit': '50', 'total_deficit_days': 3,
                           'gaps': [
                               {'start_date': '2026-10-26', 'status': 'recovered', 'minimum_balance': '-50',
                                'maximum_deficit': '50', 'duration_days': 1, 'recovery_date': '2026-10-27'},
                               {'start_date': '2026-11-22', 'status': 'ongoing', 'minimum_balance': '-10',
                                'maximum_deficit': '10', 'duration_days': 2, 'recovery_date': None},
                           ]}
            page.route('**/api/forecast/cash-gaps', lambda route: route.fulfill(content_type='application/json', body=json.dumps(gap_fixture)))
            page.reload()
            expect(page.locator('#gaps-content .gap-card')).to_have_count(2, timeout=60000)
            expect(page.locator('#gaps-content')).to_contain_text('Восстановлен')
            expect(page.locator('#gaps-content')).to_contain_text('Не найдено в прогнозном окне')
            results.append('multiple-gap-display-ui-fixture-only')
            page.unroute('**/api/forecast/cash-gaps')
            page.reload()
            expect(page.locator('#refresh-all')).to_be_enabled(timeout=60000)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(base + '/#overview')
            expect(page.locator('#refresh-all')).to_be_enabled(timeout=60000)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Горизонтальный overflow на мобильном экране'
            if args.screenshot:
                page.screenshot(path=str(args.screenshot.with_name('sprint6-mobile.png')), full_page=False)
            # XLSX проверяется на всех исходных данных C071; новый финансовый набор не создается.
            import pandas as pd
            source = args.dataset_dir / 'C071.csv'
            with tempfile.TemporaryDirectory(prefix='cashflow-ui-xlsx-', dir=root / '.venv') as temporary:
                assert Path(temporary).resolve().is_relative_to((root / '.venv').resolve())
                workbook = Path(temporary) / 'C071-check.xlsx'
                pd.read_csv(source, sep=';', encoding='utf-8-sig', dtype=str, keep_default_na=False).to_excel(workbook, index=False, engine='openpyxl')
                page.locator('#statement-file').set_input_files(str(workbook))
                page.locator('#upload-submit').click()
                expect(page.locator('#upload-message')).to_contain_text('Обработка завершена', timeout=60000)
                expect(page.locator('#metric-transactions')).to_have_text('614')
                expect(page.locator('#transactions-content')).to_contain_text('C071-check.xlsx')
                expect(page.locator('#forecast-content table').first.locator('tbody tr')).to_have_count(30)
            assert hashlib.sha256(source.read_bytes()).hexdigest() == datasets[0]['sha256']
            results.append('xlsx-full-C071-data-upload-and-analysis')
            assert not errors, errors
            results.append('mobile-390px-and-no-javascript-errors')
            browser.close()
        report = {'status': 'passed', 'browser_channel': args.browser_channel, 'scenarios': results, 'datasets': datasets,
                  'javascript_errors': errors, 'desktop_viewport': '1440x1000', 'mobile_viewport': '390x844'}
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == '__main__':
    main()
