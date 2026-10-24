import test from 'node:test';
import assert from 'node:assert/strict';
import { money, dateLabel, escapeHTML, gapState, pageItems, chartMarkup } from '../presentation.js';

test('деньги сохраняют большие значения и различают ноль и отсутствие', () => {
  assert.equal(money(null), 'Нет данных');
  assert.equal(money('0'), '0,00 ₽');
  assert.equal(money('-500.25'), '−500,25 ₽');
  assert.equal(money('999999999999999999.99').replace(/\s/g, ''), '999999999999999999,99₽');
  assert.equal(money('NaN'), 'Нет данных');
});
test('даты не сдвигаются часовым поясом и проверяют календарь', () => {
  assert.equal(dateLabel('2024-02-29'), '29.02.2024');
  assert.equal(dateLabel('2026-02-30'), 'Нет данных');
  assert.equal(dateLabel(null), 'Нет данных');
});
test('данные банковской выписки экранируются', () => {
  assert.equal(escapeHTML('<script>"&</script>'), '&lt;script&gt;&quot;&amp;&lt;/script&gt;');
});
test('неизвестный анализ не превращается в отсутствие разрыва', () => {
  assert.equal(gapState(null).title, 'Не рассчитано');
  assert.equal(gapState({ status: 'not_available' }).tone, 'warning');
  assert.equal(gapState({ status: 'no_gaps' }).tone, 'success');
  assert.equal(gapState({ status: 'detected', gap_count: 2 }).title, 'Обнаружено разрывов: 2');
});
test('пагинация не теряет хвост и корректирует страницу после фильтра', () => {
  const result = pageItems(Array.from({ length: 614 }, (_, index) => index), 25);
  assert.equal(result.items.length, 14);
  assert.equal(result.items.at(-1), 613);
  assert.equal(pageItems([], 99).page, 1);
});
test('график поддерживает отсутствие баланса и пропуски', () => {
  assert.match(chartMarkup([]), /Нет дневных данных/);
  const chart = chartMarkup([
    { date: '2026-10-01', balance: null, cumulative_cash_flow: '1' },
    { date: '2026-10-02', balance: null, cumulative_cash_flow: null },
    { date: '2026-10-03', balance: null, cumulative_cash_flow: '-2' },
  ]);
  assert.match(chart, /Накопленное изменение/);
  assert.match(chart, /01.10.2026/);
  assert.doesNotMatch(chart, /NaN|undefined/);
});
