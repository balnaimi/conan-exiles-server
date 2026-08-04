(() => {
  'use strict';

  const legacyRoutes = {
    '#quick-start': '#native-quick-start',
    '#config-generator': 'config/',
    '#mods': 'docs/mods/',
    '#server-management': 'docs/operations/',
    '#about': 'docs/',
    '#cpu-compatibility': 'docs/operations/#cpu-compatibility-check',
    '#info': 'docs/operations/#sizing-guidance'
  };

  const legacyDestination = legacyRoutes[window.location.hash];
  const siteRoot = new URL('../', document.currentScript.src);
  const currentPath = window.location.pathname.replace(/index\.html$/, '');
  if (legacyDestination && currentPath === siteRoot.pathname) {
    window.location.replace(new URL(legacyDestination, siteRoot));
    return;
  }

  const toast = document.getElementById('toast');
  let toastTimer;
  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove('show'), 1800);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      // Continue to the temporary-textarea fallback.
    }
    const textarea = document.createElement('textarea');
    try {
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      return document.execCommand('copy');
    } finally {
      textarea.remove();
    }
  }

  document.querySelectorAll('[data-copy-target]').forEach(button => {
    button.addEventListener('click', async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      const copied = target ? await copyText(target.textContent) : false;
      showToast(copied ? 'Copied command' : 'Copy failed — select the command manually');
    });
  });
})();
