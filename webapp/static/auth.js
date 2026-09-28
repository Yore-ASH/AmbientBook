/* Login / registration forms on the landing page. */

function wireForm(selector, url, collect) {
  const form = $(selector);
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api.post(url, collect());
      window.location.href = '/';
    } catch (err) {
      toast(err.message, 'bad');
      button.disabled = false;
    }
  });
}

wireForm('#login-form', '/api/auth/login', () => ({
  username: $('#login-user').value.trim(),
  password: $('#login-pass').value,
}));

wireForm('#register-form', '/api/auth/register', () => ({
  username: $('#reg-user').value.trim(),
  display_name: $('#reg-name').value.trim(),
  password: $('#reg-pass').value,
}));
