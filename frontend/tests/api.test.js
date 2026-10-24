import test from 'node:test';
import assert from 'node:assert/strict';
import { request, contracts, forecastQuery } from '../api.js';

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
