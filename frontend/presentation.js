export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function inlineMarkdown(value) {
  const protectedParts = [];
  const hold = html => `\u0000${protectedParts.push(html) - 1}\u0000`;
  let text = String(value ?? '').replace(/`([^`]+)`/g, (_, code) => hold(`<code>${escapeHTML(code)}</code>`));
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => {
    const safeUrl = escapeHTML(url);
    return hold(`<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)}</a>`);
  });
  text = escapeHTML(text)
    .replace(/\*\*(.+?)\*\*|__(.+?)__/g, '<strong>$1$2</strong>')
    .replace(/~~(.+?)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>');
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => protectedParts[Number(index)] ?? '');
}

function tableCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
}

export function renderMarkdown(value) {
  const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
  const output = [];
  let paragraph = [], listType = '', codeLines = null, codeLanguage = '';
  const flushParagraph = () => {
    if (paragraph.length) output.push(`<p>${inlineMarkdown(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = '';
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*```([\w+-]*)\s*$/);
    if (fence) {
      flushParagraph(); closeList();
      if (codeLines !== null) {
        const languageClass = codeLanguage ? ` class="language-${escapeHTML(codeLanguage)}"` : '';
        output.push(`<pre><code${languageClass}>${escapeHTML(codeLines.join('\n'))}</code></pre>`);
        codeLines = null; codeLanguage = '';
      } else { codeLines = []; codeLanguage = fence[1]; }
      continue;
    }
    if (codeLines) { codeLines.push(line); continue; }
    if (!line.trim()) { flushParagraph(); closeList(); continue; }
    const tableDelimiter = lines[i + 1] && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1]);
    if (line.includes('|') && tableDelimiter) {
      flushParagraph(); closeList();
      const headers = tableCells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) rows.push(tableCells(lines[i++]));
      i--;
      output.push(`<div class="md-table-wrap"><table><thead><tr>${headers.map(cell => `<th>${inlineMarkdown(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, index) => `<td>${inlineMarkdown(row[index] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { flushParagraph(); closeList(); const level = heading[1].length; output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`); continue; }
    if (/^\s*(?:-{3,}|_{3,}|\*{3,})\s*$/.test(line)) { flushParagraph(); closeList(); output.push('<hr>'); continue; }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph(); closeList(); const quoted = [quote[1]];
      while (i + 1 < lines.length && /^\s*>\s?/.test(lines[i + 1])) quoted.push(lines[++i].replace(/^\s*>\s?/, ''));
      output.push(`<blockquote><p>${inlineMarkdown(quoted.join(' '))}</p></blockquote>`); continue;
    }
    const item = line.match(/^\s*(?:([-+*•])\s+|(\d+)[.)]\s+)(.+)$/);
    if (item) {
      flushParagraph(); const nextType = item[2] ? 'ol' : 'ul';
      if (listType !== nextType) { closeList(); listType = nextType; output.push(`<${listType}>`); }
      output.push(`<li>${inlineMarkdown(item[3])}</li>`); continue;
    }
    closeList(); paragraph.push(line.trim());
  }
  flushParagraph(); closeList();
  if (codeLines) output.push(`<pre><code>${escapeHTML(codeLines.join('\n'))}</code></pre>`);
  return output.join('');
}

export function money(value) {
  if (value === null || value === undefined || value === '') return 'Нет данных';
  const match = String(value).match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
  if (!match) return 'Нет данных';
  const integer = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(BigInt(match[2]));
  return `${match[1] === '-' ? '−' : ''}${integer},${(match[3] || '').padEnd(2, '0')} ₽`;
}

export function dateLabel(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Нет данных';
  const date = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return 'Нет данных';
  return new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC' }).format(date);
}

export const frequencyNames = { weekly: 'Еженедельно', biweekly: 'Раз в две недели', monthly: 'Ежемесячно', quarterly: 'Ежеквартально', yearly: 'Ежегодно', unknown: 'Не определена' };
export const statusNames = { automatic: 'Автоматически', undefined: 'Не определено', needs_review: 'Требует проверки', confirmed: 'Подтверждена', potential: 'Потенциальная', uncertain: 'Не определена', recovered: 'Восстановлен', ongoing: 'До конца прогноза' };

export function gapState(data) {
  if (!data) return { tone: 'neutral', title: 'Не рассчитано' };
  if (data.status === 'not_available') return { tone: 'warning', title: 'Анализ недоступен' };
  if (data.status === 'no_gaps') return { tone: 'success', title: 'Кассовые разрывы не обнаружены' };
  if (data.status === 'detected') return { tone: 'danger', title: `Обнаружено разрывов: ${data.gap_count ?? data.gaps?.length ?? 'Нет данных'}` };
  return { tone: 'warning', title: 'Неизвестный статус анализа' };
}

export function pageItems(items, page, size = 25) {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.max(1, Math.min(page, pages));
  return { items: items.slice((current - 1) * size, current * size), page: current, pages, total: items.length };
}

export function chartMarkup(days) {
  const validNumber = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const key = days.some(day => validNumber(day?.balance)) ? 'balance' : 'cumulative_cash_flow';
  const label = key === 'balance' ? 'Прогнозируемый остаток' : 'Накопленное изменение денежных средств';
  const values = days.map(day => validNumber(day?.[key]) && dateLabel(day?.date) !== 'Нет данных' ? Number(day[key]) : null);
  const present = values.filter(value => value !== null);
  if (!present.length) return '<div class="notice">Нет дневных данных для графика.</div>';
  const low = Math.min(0, ...present), high = Math.max(0, ...present);
  const span = high - low || 1;
  const x = index => 105 + index * 800 / Math.max(1, days.length - 1);
  const y = value => 245 - (value - low) / span * 200;
  let segments = [], current = [];
  values.forEach((value, index) => { if (value === null) { if (current.length) segments.push(current); current = []; } else current.push(`${x(index)},${y(value)}`); });
  if (current.length) segments.push(current);
  const ticks = [0, 0.5, 1].map(ratio => {
    const value = low + span * ratio;
    return `<line x1="105" x2="905" y1="${y(value)}" y2="${y(value)}" class="grid-line"/><text x="90" y="${y(value) + 4}" text-anchor="end">${escapeHTML(new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 }).format(value))}</text>`;
  }).join('');
  const lines = segments.map(points => points.length === 1 ? `<circle cx="${points[0].split(',')[0]}" cy="${points[0].split(',')[1]}" r="4" class="chart-dot"/>` : `<polyline points="${points.join(' ')}" class="chart-line"/>`).join('');
  return `<p class="chart-label"><span class="line-key"></span>${label} <span class="muted">· ₽</span></p><div class="chart"><svg viewBox="0 0 950 290" role="img" aria-label="${label} по дням. Точные значения в таблице ниже.">${ticks}${lines}<text x="105" y="275">${escapeHTML(dateLabel(days[0]?.date))}</text><text x="905" y="275" text-anchor="end">${escapeHTML(dateLabel(days.at(-1)?.date))}</text></svg></div>`;
}
