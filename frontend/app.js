import { api, forecastQuery } from './api.js';
import { escapeHTML as esc, money, dateLabel, frequencyNames, statusNames, gapState, pageItems, chartMarkup } from './presentation.js';

const $ = id => document.getElementById(id);
const blank = () => ({ status: 'idle', data: null, error: '' });
const state = { transactions: blank(), categories: blank(), recurring: blank(), forecast: blank(), gaps: blank(),
  imported: null, loadedFilename: '', selectedFile: null, busy: false, query: '', generation: 0, transactionPage: 1, recurringPage: 1, events: [] };

function notice(text, tone = '', retry = '') {
  return `<div class="notice ${esc(tone)}">${esc(text)}${retry ? `<br><button class="button secondary" data-action="retry" data-module="${esc(retry)}">Повторить запрос</button>` : ''}</div>`;
}
function moduleMessage(name) {
  const module = state[name];
  if (module.status === 'loading') return notice('Получаем данные…', 'loading');
  if (module.status === 'error') return notice(module.error, 'danger', name);
  if (module.status !== 'success') return notice('Загрузите выписку, чтобы получить результаты.');
  return '';
}
function badge(status) {
  const tone = ['automatic', 'confirmed', 'recovered'].includes(status) ? 'success' : ['potential', 'needs_review', 'ongoing'].includes(status) ? 'warning' : '';
  return `<span class="badge ${tone}">${esc(statusNames[status] || 'Нет данных')}</span>`;
}
function warnings(data) {
  const items = Array.isArray(data?.warnings) ? data.warnings : [];
  return items.length ? `<details><summary>Допущения и ограничения · ${items.length}</summary><ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : '';
}
function table(caption, heads, rows) {
  return `<div class="table-wrap" tabindex="0"><table><caption>${esc(caption)}</caption><thead><tr>${heads.map(head => `<th scope="col">${esc(head)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}
function pager(name, page) {
  return `<div class="pager"><span>Найдено: ${page.total} · Страница ${page.page} из ${page.pages}</span><div class="pager-actions"><button class="button subtle" data-action="page" data-module="${name}" data-step="-1" ${page.page <= 1 ? 'disabled' : ''}>Назад</button><button class="button subtle" data-action="page" data-module="${name}" data-step="1" ${page.page >= page.pages ? 'disabled' : ''}>Далее</button></div></div>`;
}
function metric(id, label, value, note = '', tone = '') {
  return `<div class="metric"><div class="metric-label">${esc(label)}</div><div id="metric-${id}" class="metric-value ${tone}">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`;
}

function balanceNotice(info) {
  if (!info) return '';
  const sources = { balance_eod: 'остатки на конец дня', balance_after: 'балансы после операций', statement_opening: 'начальный остаток из файла' };
  if (!['detected', 'provided'].includes(info.status)) {
    const text = info.statement_closing_balance !== null && info.statement_closing_balance !== undefined
      ? `Остаток последнего дня выписки: ${money(info.statement_closing_balance)} на ${dateLabel(info.statement_end)}. Он доступен для прогноза от этой даты; в предыдущей истории есть расхождения. ${info.message || ''}`
      : `Не удалось автоматически определить исходный остаток. Прогноз кассового разрыва может быть недоступен. ${info.message || ''}`;
    return notice(text, 'warning')
      + (Array.isArray(info.issues) && info.issues.length ? `<details><summary>Расхождения баланса</summary><ul>${info.issues.map(issue => `<li>${esc(issue)}</li>`).join('')}</ul></details>` : '');
  }
  return notice(`Остаток определен автоматически. До начала выписки: ${money(info.statement_opening_balance)}. На конец ${dateLabel(info.statement_end)}: ${money(info.statement_closing_balance)}. Источник: ${sources[info.source] || 'данные выписки'}. Для прогноза от конца выписки используется последний остаток, без повторного добавления операций.`, 'success');
}

function renderOverview() {
  const forecast = state.forecast.status === 'success' ? state.forecast.data : null;
  const summary = forecast?.summary;
  const count = state.transactions.status === 'success' ? state.transactions.data.length : null;
  const gap = state.gaps.status === 'success' ? gapState(state.gaps.data).title : state.gaps.status === 'error' ? 'Ошибка анализа разрывов' : 'Анализ разрывов не выполнен';
  const period = forecast ? `${dateLabel(forecast.start_date)} — ${dateLabel(forecast.end_date)}` : 'Прогноз не рассчитан';
  $('overview-content').innerHTML = `<div class="metrics">${[
    metric('opening', 'Исходный остаток', money(summary?.opening_balance), forecast ? `${forecast.opening_balance_source === 'provided' ? 'Введен пользователем' : forecast.opening_balance_source === 'history_eod' ? 'Из выписки' : forecast.opening_balance_source === 'statement_reported' ? 'Из последнего дня · есть замечания к истории' : 'Не определен'} · на конец ${dateLabel(forecast.as_of)}` : 'На дату расчета'),
    metric('income', 'Ожидаемые поступления', money(summary?.total_income), 'За 30 дней прогноза', 'income'),
    metric('expense', 'Ожидаемые расходы', money(summary?.total_expense), 'За 30 дней прогноза'),
    metric('net', 'Чистый денежный поток', money(summary?.net_cash_flow), 'Ожидаемые поступления минус расходы'),
    metric('closing', 'Прогнозируемый остаток', money(summary?.closing_balance), 'На конец прогнозного периода'),
    metric('transactions', 'Операций в выписке', count === null ? 'Нет данных' : String(count), state.loadedFilename || 'Последняя выписка в памяти сервера'),
  ].join('')}</div><div class="overview-meta"><span>${esc(period)}</span><span>${esc(gap)}</span>${state.recurring.status === 'success' ? `<span>Подтвержденных серий: ${esc(state.recurring.data.confirmed_groups ?? 'Нет данных')}</span>` : ''}</div>`;
}

function renderImport() {
  const data = state.imported;
  if (!data) { $('import-result').innerHTML = ''; return; }
  const errors = Array.isArray(data.errors) ? data.errors : [];
  $('import-result').innerHTML = notice(`${data.message || 'Выписка обработана'}. Всего операций: ${data.total_transactions}; без ошибок: ${data.valid_transactions ?? 'Нет данных'}; с ошибками данных: ${data.invalid_transactions ?? 'Нет данных'}; повторений: ${data.duplicates ?? 'Нет данных'}.`, data.status === 'partial_success' ? 'warning' : 'success')
    + balanceNotice(data.balance_info)
    + (errors.length ? `<details><summary>Проверить ошибки строк · ${errors.length}</summary><ul>${errors.map(error => `<li>Строка ${esc(error.source_row)}${error.operation_id ? ` · ${esc(error.operation_id)}` : ''}: ${esc(error.message)}</li>`).join('')}</ul></details>` : '') + warnings(data);
}

function renderTransactions() {
  $('category-status').innerHTML = state.categories.status === 'error' ? moduleMessage('categories') : state.categories.status === 'loading' ? notice('Назначаем категории…', 'loading') : '';
  const message = moduleMessage('transactions');
  if (message) { $('transactions-content').innerHTML = message; return; }
  const search = $('transaction-search').value.toLocaleLowerCase('ru');
  const direction = $('transaction-direction').value;
  const rows = state.transactions.data.filter(item => (direction === 'all' || item.direction === direction)
    && `${item.description ?? ''} ${item.counterparty ?? ''} ${item.inn ?? ''}`.toLocaleLowerCase('ru').includes(search));
  const categories = new Map((state.categories.status === 'success' ? state.categories.data.categorizations : []).filter(Boolean).map(item => [item.transaction_id, item]));
  const sources = new Map((Array.isArray(state.imported?.source_rows) ? state.imported.source_rows : []).filter(Boolean).map(item => [item.transaction_id, item]));
  const page = pageItems(rows, state.transactionPage); state.transactionPage = page.page;
  if (!rows.length) { $('transactions-content').innerHTML = notice(state.transactions.data.length ? 'Нет операций по выбранному фильтру.' : 'В рабочем пространстве пока нет операций.'); return; }
  $('transactions-content').innerHTML = table('Транзакции банковской выписки', ['Дата', 'Операция и контрагент', 'Сумма', 'Направление', 'Категория', 'Источник'], page.items.map(item => {
    const category = categories.get(item.id), source = sources.get(item.id);
    return `<tr><td class="number">${esc(dateLabel(item.date))}</td><td class="description">${esc(item.description || 'Нет описания')}<small>${esc(item.counterparty || 'Нет контрагента')} · ИНН: ${esc(item.inn || 'Не указан')}</small></td><td class="number ${item.direction === 'income' ? 'income' : 'expense'}">${esc(money(item.amount))}</td><td>${esc(item.direction === 'income' ? 'Поступление' : item.direction === 'expense' ? 'Списание' : 'Нет данных')}</td><td>${esc(category?.category_name || 'Не рассчитана')}<small class="muted" style="display:block;margin-top:6px">${category ? badge(category.status) : ''}</small>${category?.reason ? `<details><summary>Почему</summary><p>${esc(category.reason)}</p><p class="muted">${esc(category.source || '')} · ${esc((Array.isArray(category.matched_rules) ? category.matched_rules : []).join(', '))}</p></details>` : ''}</td><td class="description">${esc(state.loadedFilename || 'Выписка в памяти')}<small>${source ? `Строка ${esc(source.source_row)} · ${esc(source.operation_id || `ID ${item.id}`)}` : `ID ${esc(item.id)}`}</small></td></tr>`;
  })) + pager('transactions', page);
}

function renderRecurring() {
  const message = moduleMessage('recurring');
  if (message) { $('recurring-content').innerHTML = message; return; }
  const data = state.recurring.data;
  const filter = $('recurring-filter').value;
  const groups = data.groups.filter(item => item && (filter === 'all' || item.status === filter));
  const page = pageItems(groups, state.recurringPage); state.recurringPage = page.page;
  const counts = `<div class="mini-metrics"><span>Подтверждено: <strong>${esc(data.confirmed_groups ?? 'Нет данных')}</strong></span><span>Потенциальные: <strong>${esc(data.potential_groups ?? 'Нет данных')}</strong></span><span>Неопределенные: <strong>${esc(data.uncertain_groups ?? 'Нет данных')}</strong></span></div>`;
  $('recurring-content').innerHTML = counts + (!groups.length ? notice('Повторные группы по выбранному фильтру не найдены.') : table('Повторяющиеся операции', ['Описание', 'Средняя сумма', 'Период', 'Последняя / следующая дата', 'Статус'], page.items.map(group => `<tr><td class="description">${esc(group.description || 'Нет описания')}<small>${esc(group.counterparty || '')} · ${esc(group.category_name || 'Не определено')}</small><details><summary>Обоснование · ${esc(group.repetitions ?? 'Нет данных')} операций</summary><p>${esc(group.reason || 'Нет данных')}</p><p>Диапазон: ${esc(money(group.minimum_amount))} — ${esc(money(group.maximum_amount))}</p><p>${group.usable_for_calculations === true ? 'Пригодна для расчетов по правилам анализа' : 'Требует проверки перед использованием в расчетах'}</p></details></td><td class="number">${esc(money(group.average_amount))}<small class="muted" style="display:block">${esc(group.direction === 'income' ? 'Поступление' : group.direction === 'expense' ? 'Списание' : 'Нет данных')}</small></td><td>${esc(frequencyNames[group.frequency] || 'Не определена')}</td><td class="number">${esc(dateLabel(group.last_date))}<small class="muted" style="display:block;margin-top:5px">→ ${esc(group.next_expected_date ? dateLabel(group.next_expected_date) : 'Не определена')}</small></td><td>${badge(group.status)}</td></tr>`)) + pager('recurring', page));
}

function renderForecast() {
  const message = moduleMessage('forecast');
  if (message) { $('forecast-content').innerHTML = message; return; }
  const data = state.forecast.data;
  const days = data.daily.filter(Boolean);
  $('forecast-content').innerHTML = `<div class="mini-metrics"><span>Период: <strong>${esc(dateLabel(data.start_date))} — ${esc(dateLabel(data.end_date))}</strong></span><span>Дневных строк: <strong>${days.length}</strong></span></div>`
    + (data.as_of_source === 'statement_end' ? notice(`Автоматический расчет от конца выписки ${dateLabel(data.as_of)}. Это исторический срез, а не текущий остаток счета.`) : '')
    + balanceNotice(data.balance_info)
    + (data.summary.opening_balance === null || data.summary.opening_balance === undefined ? notice('Исходный остаток неизвестен. Показано изменение денежных средств; абсолютный баланс не рассчитан.', 'warning') : '')
    + (days.length !== 30 ? notice('Прогноз содержит неполный набор дневных данных. Показаны только полученные строки.', 'warning') : '')
    + chartMarkup(days) + warnings(data)
    + `<details><summary>Ежедневные показатели</summary>${days.length ? table('Ежедневный прогноз', ['Дата', 'Поступления', 'Расходы', 'Чистый поток', 'Остаток'], days.map(day => `<tr><td class="number">${esc(dateLabel(day.date))}</td><td class="number income">${esc(money(day.income))}</td><td class="number">${esc(money(day.expense))}</td><td class="number">${esc(money(day.net_cash_flow))}</td><td class="number">${esc(money(day.balance))}</td></tr>`)) : notice('Нет ежедневных значений.')}</details>`
    + (Array.isArray(data.categories) && data.categories.length ? `<details><summary>Прогноз по категориям</summary>${table('Прогноз по категориям', ['Категория', 'Поступления', 'Расходы', 'Чистый поток'], data.categories.filter(Boolean).map(item => `<tr><td>${esc(item.category_name || 'Не определено')}</td><td class="number">${esc(money(item.income))}</td><td class="number">${esc(money(item.expense))}</td><td class="number">${esc(money(item.net_cash_flow))}</td></tr>`))}</details>` : '');
}

function renderGaps() {
  const message = moduleMessage('gaps');
  if (message) { $('gaps-content').innerHTML = message; return; }
  const data = state.gaps.data, view = gapState(data);
  $('gaps-content').innerHTML = notice(view.title, view.tone)
    + (data.status === 'not_available' ? '<p class="muted">Отсутствие результата не означает отсутствие кассовых разрывов. Проверьте доступность исходного остатка и ограничения анализа.</p>' : `<div class="mini-metrics"><span>Минимальный остаток: <strong>${esc(money(data.minimum_balance))}</strong></span><span>Максимальный дефицит: <strong>${esc(money(data.maximum_deficit))}</strong></span><span>Отрицательных дней: <strong>${esc(data.total_deficit_days ?? 'Нет данных')}</strong></span></div>`)
    + data.gaps.filter(Boolean).map(gap => `<article class="gap-card"><h3>Разрыв с ${esc(dateLabel(gap.start_date))} ${badge(gap.status)}</h3><div class="mini-metrics"><span>Дефицит: <strong>${esc(money(gap.maximum_deficit))}</strong></span><span>Минимум: <strong>${esc(money(gap.minimum_balance))}</strong></span><span>Длительность: <strong>${esc(gap.duration_days ?? 'Нет данных')} дн.</strong></span><span>Восстановление: <strong>${esc(gap.recovery_date ? dateLabel(gap.recovery_date) : 'Не найдено в прогнозном окне')}</strong></span></div>${gap.starts_before_forecast ? '<p class="muted">Исходный остаток уже был отрицательным; начало до прогноза неизвестно.</p>' : ''}<details><summary>Потоки и связанные события</summary><p>В день начала: поступления ${esc(money(gap.start_day?.income))}; расходы ${esc(money(gap.start_day?.expense))}.</p><p>В день восстановления: поступления ${esc(money(gap.recovery_day?.income))}; расходы ${esc(money(gap.recovery_day?.expense))}.</p>${Array.isArray(gap.related_events) && gap.related_events.length ? table('Связанные регулярные события', ['Дата', 'Категория', 'Направление', 'Сумма'], gap.related_events.filter(Boolean).map(event => `<tr><td>${esc(dateLabel(event.date))}</td><td>${esc(event.category || 'Не определено')}</td><td>${esc(event.direction === 'income' ? 'Поступление' : 'Списание')}</td><td class="number">${esc(money(event.amount))}</td></tr>`)) : '<p class="muted">Конкретные прогнозные события не предоставлены; доступны дневные показатели.</p>'}<p class="muted">Это контекст изменения остатка, а не доказанная причина дефицита.</p></details></article>`).join('') + warnings(data);
}

function renderEvents() {
  const root = $('events-content');
  if (!state.events.length) { root.innerHTML = notice('Будущих событий пока нет.'); return; }
  root.innerHTML = table('Будущие события', ['Дата', 'Событие', 'Направление', 'Сумма', 'Источник', ''], state.events.map(event => `<tr><td class="number">${esc(dateLabel(event.date))}</td><td>${esc(event.description)}<small class="muted" style="display:block">${esc(event.category || 'other')}</small></td><td>${event.direction === 'income' ? 'Поступление' : 'Списание'}</td><td class="number">${esc(money(event.amount))}</td><td>Ручной ввод</td><td><button class="button subtle" data-action="delete-event" data-id="${event.id}">Удалить</button></td></tr>`));
}
function render() { renderOverview(); renderImport(); renderTransactions(); renderRecurring(); renderForecast(); renderGaps(); renderEvents(); }
function setBusy(busy) {
  state.busy = busy;
  ['upload-submit', 'statement-file', 'refresh-all', 'as-of', 'opening-balance'].forEach(id => { $(id).disabled = busy; });
  document.querySelectorAll('[data-action="retry"]').forEach(button => { button.disabled = busy; });
}
async function loadModule(name, generation) {
  state[name] = { ...blank(), status: 'loading' }; render(); setBusy(state.busy);
  try {
    const data = await (['forecast', 'gaps'].includes(name) ? api[name](state.query) : api[name]());
    if (generation === state.generation) state[name] = { status: 'success', data, error: '' };
  } catch (error) {
    if (generation === state.generation) state[name] = { status: 'error', data: null, error: error.message };
  }
  if (generation === state.generation) { render(); setBusy(state.busy); }
}
async function analyze() {
  const generation = state.generation;
  await loadModule('categories', generation);
  await Promise.allSettled(['recurring', 'forecast', 'gaps'].map(name => loadModule(name, generation)));
}
function clearAnalysis() {
  ['categories', 'recurring', 'forecast', 'gaps'].forEach(name => { state[name] = blank(); });
  state.transactionPage = state.recurringPage = 1;
}

async function loadExisting() {
  if (state.busy) return;
  setBusy(true); state.transactions = { ...blank(), status: 'loading' }; render();
  try {
    const [rows, events] = await Promise.all([api.transactions(), api.events()]);
    state.events = events;
    state.transactions = { status: 'success', data: rows, error: '' };
    state.generation++; clearAnalysis(); render();
    if (rows.length) { $('global-message').innerHTML = notice('Загружена последняя выписка из памяти сервера. Имя файла и исходные номера строк после обновления страницы недоступны.'); await analyze(); }
  } catch (error) { state.transactions = { status: 'error', data: null, error: error.message }; render(); }
  finally { setBusy(false); }
}

function chooseFile(file) {
  state.selectedFile = file || null;
  $('file-info').textContent = file ? `${file.name} · ${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(file.size / 1024 / 1024)} МиБ` : 'Файл не выбран';
  $('upload-message').innerHTML = '';
}
$('statement-file').addEventListener('change', event => chooseFile(event.target.files[0]));
$('file-zone').addEventListener('dragover', event => { event.preventDefault(); if (!state.busy) $('file-zone').classList.add('dragover'); });
$('file-zone').addEventListener('dragleave', () => $('file-zone').classList.remove('dragover'));
$('file-zone').addEventListener('drop', event => {
  event.preventDefault(); $('file-zone').classList.remove('dragover');
  if (state.busy) return;
  const file = event.dataTransfer.files[0];
  if (file) { const transfer = new DataTransfer(); transfer.items.add(file); $('statement-file').files = transfer.files; chooseFile(file); }
});
$('upload-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  const file = state.selectedFile;
  if (!file) { $('upload-message').innerHTML = notice('Сначала выберите файл.', 'warning'); return; }
  if (!/\.(csv|xlsx)$/i.test(file.name) || !file.size || file.size > 10 * 1024 * 1024) {
    $('upload-message').innerHTML = notice('Нужен непустой CSV или XLSX размером до 10 МиБ.', 'danger'); return;
  }
  setBusy(true); $('upload-message').innerHTML = notice('Загружаем и проверяем выписку…', 'loading');
  try {
    const query = forecastQuery($('as-of').value, $('opening-balance').value);
    const result = await api.upload(file);
    state.query = query;
    state.generation++; clearAnalysis(); state.imported = result; state.loadedFilename = file.name;
    state.transactions = { status: 'success', data: result.transactions, error: '' };
    $('global-message').innerHTML = '';
    $('upload-message').innerHTML = notice('Выписка принята. Получаем результаты анализа…', 'success');
    render(); await analyze();
    $('upload-message').innerHTML = notice('Обработка завершена. Результаты доступны ниже; проверьте сообщения отдельных модулей.', 'success');
  } catch (error) { $('upload-message').innerHTML = notice(`${error.message}${state.transactions.data?.length ? ' Показана предыдущая успешно загруженная выписка.' : ''}`, 'danger'); }
  finally { setBusy(false); }
});
$('forecast-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  if (!state.transactions.data?.length) { $('settings-message').textContent = 'Сначала загрузите выписку.'; return; }
  try { state.query = forecastQuery($('as-of').value, $('opening-balance').value); }
  catch (error) { $('settings-message').textContent = error.message; return; }
  $('settings-message').textContent = 'Обновляем результаты…'; setBusy(true);
  try {
    await Promise.allSettled(['forecast', 'gaps'].map(name => loadModule(name, state.generation)));
    $('settings-message').textContent = 'Запросы завершены. Статус каждого модуля показан в его разделе.';
  }
  finally { setBusy(false); }
});
['as-of', 'opening-balance'].forEach(id => $(id).addEventListener('input', () => { $('settings-message').textContent = 'Параметры изменены. Нажмите «Обновить результаты», чтобы применить их.'; }));
$('transaction-search').addEventListener('input', () => { state.transactionPage = 1; renderTransactions(); });
$('transaction-direction').addEventListener('change', () => { state.transactionPage = 1; renderTransactions(); });
$('recurring-filter').addEventListener('change', () => { state.recurringPage = 1; renderRecurring(); });
document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const name = button.dataset.module;
  if (button.dataset.action === 'page') {
    state[name === 'transactions' ? 'transactionPage' : 'recurringPage'] += Number(button.dataset.step);
    name === 'transactions' ? renderTransactions() : renderRecurring();
  } else if (button.dataset.action === 'retry' && !state.busy) {
    if (name === 'transactions') { await loadExisting(); return; }
    if (!state.transactions.data?.length) return;
    setBusy(true);
    try { await loadModule(name, state.generation); } finally { setBusy(false); }
  }
});
const tabScroll = new Map();
let activeSection = null;
function activeNav() {
  const hash = location.hash || '#overview';
  const target = document.querySelector(hash);
  const section = target?.matches('.section') ? target : document.querySelector('#overview');
  const scroller = $('tab-content');
  if (activeSection && activeSection !== section) tabScroll.set(activeSection.id, scroller.scrollTop);
  document.querySelectorAll('.tab-content>.section').forEach(item => item.classList.toggle('is-active', item === section));
  activeSection = section;
  scroller.scrollTop = tabScroll.get(section.id) || 0;
  document.querySelectorAll('nav a').forEach(link => { if (link.getAttribute('href') === `#${section.id}`) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
}
window.addEventListener('hashchange', activeNav); activeNav(); render(); loadExisting();

const aiPanel = $('ai-panel'), aiBackdrop = $('ai-backdrop'), aiOpen = $('ai-open');
function closeAssistant() {
  document.body.classList.remove('ai-panel--open'); aiPanel.classList.remove('ai-panel--open');
  aiPanel.setAttribute('aria-hidden', 'true'); aiOpen.setAttribute('aria-expanded', 'false');
  window.setTimeout(() => { if (!aiPanel.classList.contains('ai-panel--open')) aiBackdrop.hidden = true; }, 260);
  aiOpen.focus();
}
function openAssistant() {
  aiBackdrop.hidden = false;
  requestAnimationFrame(() => { document.body.classList.add('ai-panel--open'); aiPanel.classList.add('ai-panel--open'); });
  aiPanel.setAttribute('aria-hidden', 'false'); aiOpen.setAttribute('aria-expanded', 'true');
  $('chat-message').focus();
}
aiOpen.addEventListener('click', openAssistant);
$('ai-close').addEventListener('click', closeAssistant);
aiBackdrop.addEventListener('click', closeAssistant);
document.addEventListener('keydown', event => { if (event.key === 'Escape' && aiPanel.classList.contains('ai-panel--open')) closeAssistant(); });

$('event-form').addEventListener('submit', async event => {
  event.preventDefault();
  const amount = $('event-amount').value.trim().replaceAll(' ', '').replace(',', '.');
  if (!/^[0-9]+(?:\.[0-9]{1,2})?$/.test(amount) || Number(amount) <= 0) { $('event-message').textContent = 'Введите положительную сумму, например 12500,50.'; return; }
  try {
    const created = await api.addEvent({ date: $('event-date').value, description: $('event-description').value.trim(), direction: $('event-direction').value, amount, category: $('event-category').value.trim() || 'other' });
    state.events.push(created); renderEvents();
    if (state.transactions.data?.length) { state.query = forecastQuery($('as-of').value, $('opening-balance').value); await Promise.allSettled(['forecast', 'gaps'].map(name => loadModule(name, state.generation))); $('event-message').textContent = 'Событие добавлено; прогноз и анализ рисков пересчитаны.'; }
    else $('event-message').textContent = 'Событие добавлено. Загрузите выписку, чтобы учесть его в прогнозе.';
  } catch (error) { $('event-message').textContent = error.message; }
});

$('chat-form').addEventListener('submit', async event => {
  event.preventDefault(); const question = $('chat-message').value.trim(); $('chat-submit').disabled = true; $('chat-status').textContent = 'Ищем подтверждение в данных…';
  $('chat-answer').insertAdjacentHTML('beforeend', `<div class="ai-message ai-message-user">${esc(question)}</div>`);
  try {
    const result = await api.chat(question);
    $('chat-answer').insertAdjacentHTML('beforeend', `<div class="notice ai-message">${esc(result.answer).replaceAll('\n', '<br>')}<p class="muted">${result.mode === 'llm' ? 'Ответ сформирован языковой моделью по контексту системы.' : 'Ответ сформирован по данным системы.'}</p>${result.sources.length ? `<details><summary>Источники · ${result.sources.length}</summary><ul>${result.sources.map(source => `<li>${esc(source)}</li>`).join('')}</ul></details>` : ''}</div>`);
    $('chat-message').value = '';
    $('ai-messages').scrollTop = $('ai-messages').scrollHeight;
    $('chat-status').textContent = '';
  } catch (error) { $('chat-status').textContent = error.message; }
  finally { $('chat-submit').disabled = false; }
});

document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action="delete-event"]');
  if (!button) return;
  try { await api.deleteEvent(button.dataset.id); state.events = state.events.filter(item => item.id !== Number(button.dataset.id)); renderEvents(); $('event-message').textContent = 'Событие удалено.'; }
  catch (error) { $('event-message').textContent = error.message; }
});
