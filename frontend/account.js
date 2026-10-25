import { request } from './api.js';

const name = document.querySelector('#account-name');
const logout = document.querySelector('#account-logout');
try {
  const user = await request('/api/auth/me');
  name.textContent = user.name;
  name.title = user.email;
} catch (error) {
  name.textContent = error.message;
}
logout.addEventListener('click', async () => {
  logout.disabled = true;
  try {
    await request('/api/auth/logout', { method: 'POST' });
    window.location.replace('/login');
  } catch (error) {
    name.textContent = error.message;
    logout.disabled = false;
  }
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
