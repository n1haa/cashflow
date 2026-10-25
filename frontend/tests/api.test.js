import test from 'node:test';
import assert from 'node:assert/strict';
import { api, request, contracts, forecastQuery } from '../api.js';

test('параметры передают исходные денежные строки без float', () => {
  assert.equal(forecastQuery('', ''), '');
  assert.equal(new URLSearchParams(forecastQuery('2026-09-30', '1 000,50').slice(1)).get('opening_balance'), '1000.50');
  assert.equal(new URLSearchParams(forecastQuery('', '0').slice(1)).get('opening_balance'), '0');
  assert.throws(() => forecastQuery('', 'NaN'), /Введите остаток/);
});
test('успешный ответ валидируется', async () => {
  assert.deepEqual(await request('/x', {}, contracts.transactions, async () => new Response('[{"id":1}]')), [{ id: 1 }]);
  await assert.rejects(request('/x', {}, contracts.forecast, async () => new Response('{}')), /неполон/);
});
test('ошибки API и недоступный модуль остаются явными', async () => {
  await assert.rejects(request('/x', {}, () => true, async () => new Response('{"detail":"Файл пустой"}', { status: 400 })), /Файл пустой/);
  await assert.rejects(request('/x', {}, () => true, async () => new Response('{}', { status: 404 })), /недоступен/);
  await assert.rejects(request('/x', {}, () => true, async () => new Response('<html>')), /неизвестном формате/);
});
test('сетевая ошибка и таймаут завершают запрос', async () => {
  await assert.rejects(request('/x', {}, () => true, async () => { throw new TypeError(); }), /Нет связи/);
  const blocked = (_url, { signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  await assert.rejects(request('/x', {}, () => true, blocked, 5), /не ответил/);
});
test('ручные регулярные правила создаются, обновляются и удаляются', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return options.method === 'DELETE'
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify({ id: 'rule-1' }), { status: options.method === 'POST' ? 201 : 200 });
  };
  try {
    const body = { description: 'Аренда', amount: '100.00' };
    assert.equal((await api.saveRecurringRule(body)).id, 'rule-1');
    assert.equal((await api.saveRecurringRule(body, 'rule-1')).id, 'rule-1');
    assert.equal(await api.deleteRecurringRule('rule-1'), null);
    assert.deepEqual(calls.map(call => call.options.method), ['POST', 'PUT', 'DELETE']);
    assert.equal(calls[0].path, '/api/transactions/recurring-rules');
    assert.equal(calls[1].path, '/api/transactions/recurring-rules/rule-1');
  } finally { globalThis.fetch = originalFetch; }
});
