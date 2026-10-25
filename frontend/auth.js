import { request } from './api.js';

const registration = window.location.pathname === '/register';
const form = document.querySelector('#auth-form');
const message = document.querySelector('#auth-message');
const submit = document.querySelector('#auth-submit');
const password = form.elements.password;
const confirmation = form.elements.password_confirmation;
document.querySelector(registration ? '#register-tab' : '#login-tab').setAttribute('aria-current', 'page');
for (const id of ['name-field', 'confirmation-field', 'password-hint']) document.getElementById(id).hidden = !registration;
form.elements.name.required = registration;
form.elements.name.disabled = !registration;
confirmation.required = registration;
confirmation.disabled = !registration;
if (registration) {
  document.title = 'Регистрация — CashFlow AI';
  document.querySelector('#auth-title').textContent = 'Создайте аккаунт';
  document.querySelector('#auth-description').textContent = 'Начните с личного пространства для ваших финансов.';
  password.autocomplete = 'new-password';
  password.minLength = 12;
  password.setAttribute('aria-describedby', 'password-hint');
  submit.textContent = 'Создать аккаунт →';
}
document.querySelector('#toggle-password').addEventListener('click', event => {
  const show = password.type === 'password';
  password.type = show ? 'text' : 'password';
  event.currentTarget.textContent = show ? 'Скрыть' : 'Показать';
  event.currentTarget.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
  event.currentTarget.setAttribute('aria-pressed', String(show));
});
form.addEventListener('input', () => {
  confirmation.setCustomValidity('');
  message.hidden = true;
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (registration && password.value !== confirmation.value) {
    confirmation.setCustomValidity('Пароли не совпадают.');
    confirmation.reportValidity();
    return;
  }
  const payload = Object.fromEntries(new FormData(form));
  const label = submit.textContent;
  submit.disabled = true;
  submit.textContent = registration ? 'Создаём аккаунт…' : 'Входим…';
  message.hidden = true;
  try {
    await request(`/api/auth/${registration ? 'register' : 'login'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    window.location.replace('/dashboard');
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
    message.focus();
  } finally {
    submit.disabled = false;
    submit.textContent = label;
  }
});
