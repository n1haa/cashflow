export class ApiError extends Error {
  constructor(message, status = null) { super(message); this.name = 'ApiError'; this.status = status; }
}

const array = value => Array.isArray(value);
export const contracts = {
  transactions: data => array(data) && data.every(row => row && Number.isInteger(row.id)),
  upload: data => data && ['success', 'partial_success'].includes(data.status) && array(data.transactions)
    && Number.isInteger(data.total_transactions) && data.total_transactions === data.transactions.length,
  categories: data => data && array(data.categorizations) && Number.isInteger(data.total_transactions),
  recurring: data => data && array(data.groups) && Number.isInteger(data.total_groups),
  forecast: data => data && array(data.daily) && data.summary && typeof data.summary === 'object' && typeof data.start_date === 'string',
  gaps: data => data && ['detected', 'no_gaps', 'not_available'].includes(data.status) && array(data.gaps),
  events: data => array(data) && data.every(event => event && Number.isInteger(event.id)),
  event: data => data && Number.isInteger(data.id),
  chat: data => data && typeof data.answer === 'string' && array(data.sources),
};

export async function request(path, options = {}, validate = () => true, fetcher = globalThis.fetch, timeout = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetcher(path, { credentials: 'same-origin', ...options, signal: controller.signal, headers: { Accept: 'application/json', 'X-Requested-With': 'CashFlow', ...options.headers } });
    if (response.status === 401 && !['/api/auth/login', '/api/auth/register'].includes(path) && globalThis.window) {
      window.location.replace('/login');
    }
    if (response.status === 204) return null;
    let data;
    try { data = await response.json(); }
    catch { throw new ApiError('Сервер вернул ответ в неизвестном формате. Повторите запрос.', response.status); }
    if (!response.ok) {
      let detail = data?.detail;
      if (response.status === 404 && detail === 'Not Found') detail = 'Модуль недоступен на сервере.';
      if (Array.isArray(detail)) detail = detail.map(item => item.msg || 'Некорректные параметры').join('; ');
      if (typeof detail !== 'string') detail = response.status === 404 ? 'Модуль недоступен на сервере.' : 'Не удалось получить данные от сервера.';
      throw new ApiError(detail, response.status);
    }
    if (!validate(data)) throw new ApiError('Ответ сервера неполон или не соответствует формату модуля.', response.status);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === 'AbortError') throw new ApiError('Сервер не ответил за 60 секунд. Повторите запрос.');
    throw new ApiError('Нет связи с сервером. Проверьте соединение и повторите запрос.');
  } finally { clearTimeout(timer); }
}

export function forecastQuery(asOf = '', openingBalance = '') {
  const params = new URLSearchParams();
  if (asOf) params.set('as_of', asOf);
  const amount = openingBalance.trim().replaceAll('\u00a0', '').replaceAll(' ', '').replace(',', '.');
  if (amount && !/^[+-]?\d+(?:\.\d+)?$/.test(amount)) throw new ApiError('Введите остаток числом, например 100000,50.');
  if (amount) params.set('opening_balance', amount);
  return params.size ? `?${params}` : '';
}

export const api = {
  transactions: () => request('/api/transactions', {}, contracts.transactions),
  upload(file) { const body = new FormData(); body.append('file', file); return request('/api/transactions/upload', { method: 'POST', body }, contracts.upload); },
  categories: () => request('/api/transactions/categorize', { method: 'POST' }, contracts.categories),
  recurring: () => request('/api/transactions/recurring', {}, contracts.recurring),
  forecast: query => request(`/api/forecast${query}`, {}, contracts.forecast),
  gaps: query => request(`/api/forecast/cash-gaps${query}`, {}, contracts.gaps),
  events: () => request('/api/events', {}, contracts.events),
  addEvent(payload) { return request('/api/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, contracts.event); },
  deleteEvent(id) { return request(`/api/events/${id}`, { method: 'DELETE' }); },
  chat(message) { return request('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }) }, contracts.chat); },
};
