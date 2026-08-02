#!/usr/bin/env node
/** Browser-level regression checks for the self-contained GitHub Pages UI. */

import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createServer as createNetServer } from 'node:net';
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const DOCS = join(ROOT, 'docs');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.jpg': 'image/jpeg',
  '.png': 'image/png',
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function chromeExecutable() {
  const candidates = [
    process.env.CHROME_BIN,
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    'google-chrome',
    'chromium',
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (candidate.includes('/') && spawnSync('test', ['-x', candidate]).status === 0) return candidate;
    const found = spawnSync('sh', ['-c', `command -v "$1"`, 'sh', candidate], { encoding: 'utf8' });
    if (found.status === 0 && found.stdout.trim()) return found.stdout.trim();
  }
  throw new Error('Chrome/Chromium executable not found');
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = createNetServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(error => (error ? reject(error) : resolvePort(port)));
    });
  });
}

function startStaticServer() {
  const server = createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
    const file = resolve(DOCS, normalize(relative));
    if (!file.startsWith(`${DOCS}/`)) {
      response.writeHead(403).end('Forbidden');
      return;
    }
    try {
      if (!statSync(file).isFile()) throw new Error('Not a file');
      response.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
      response.end(readFileSync(file));
    } catch {
      response.writeHead(404).end('Not found');
    }
  });
  return new Promise((resolveServer, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolveServer(server));
  });
}

async function waitForTarget(port, expectedUrl, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = targets.find(target => target.type === 'page' && target.url.startsWith(expectedUrl));
      if (page?.webSocketDebuggerUrl) return page;
    } catch {
      // Chromium may still be starting.
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 100));
  }
  throw new Error('Timed out waiting for Chromium DevTools target');
}

function createCdp(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl);
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolveRequest, rejectRequest } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) rejectRequest(new Error(message.error.message));
    else resolveRequest(message.result);
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener('open', resolveOpen, { once: true });
    socket.addEventListener('error', rejectOpen, { once: true });
  });
  return {
    async send(method, params = {}) {
      await opened;
      const id = ++nextId;
      const result = new Promise((resolveRequest, rejectRequest) => {
        pending.set(id, { resolveRequest, rejectRequest });
      });
      socket.send(JSON.stringify({ id, method, params }));
      return result;
    },
    close() {
      socket.close();
    },
  };
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'Browser evaluation failed');
  return result.result.value;
}

let staticServer;
let chrome;
let profile;
let cdp;
try {
  staticServer = await startStaticServer();
  const pagePort = staticServer.address().port;
  const debugPort = await freePort();
  profile = mkdtempSync(join(tmpdir(), 'conan-pages-browser-'));
  chrome = spawn(chromeExecutable(), [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    `--remote-debugging-port=${debugPort}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    `http://127.0.0.1:${pagePort}/index.html#config-generator`,
  ], { stdio: 'ignore' });

  const target = await waitForTarget(debugPort, `http://127.0.0.1:${pagePort}/`);
  cdp = createCdp(target.webSocketDebuggerUrl);
  await cdp.send('Runtime.enable');
  const readiness = await evaluate(cdp, `new Promise(resolve => {
    const deadline = Date.now() + 5000;
    const inspect = () => {
      const tabBar = document.querySelector('.tab-bar');
      const active = document.querySelector('.tab-btn.active')?.dataset.tab;
      const state = {
        readyState: document.readyState,
        hash: location.hash,
        active,
        tabTop: tabBar ? Math.round(tabBar.getBoundingClientRect().top) : null
      };
      if (state.readyState === 'complete' && state.hash === '#config-generator' && active === 'config-generator' && Math.abs(state.tabTop) <= 1) {
        resolve(state);
      } else if (Date.now() >= deadline) {
        resolve(state);
      } else {
        setTimeout(inspect, 50);
      }
    };
    inspect();
  })`);
  assert(
    readiness.readyState === 'complete' && readiness.hash === '#config-generator' && readiness.active === 'config-generator' && Math.abs(readiness.tabTop) <= 1,
    `Page did not reach stable deep-link state: ${JSON.stringify(readiness)}`,
  );

  const initial = await evaluate(cdp, `(() => {
    window.__tabActivations = [];
    const original = window.activateTab;
    window.activateTab = function(...args) {
      window.__tabActivations.push(args[0]);
      return original.apply(this, args);
    };
    return {
      hash: location.hash,
      active: document.querySelector('.tab-btn.active')?.dataset.tab,
      selectedCount: document.querySelectorAll('[role="tab"][aria-selected="true"]').length,
      tabTop: Math.round(document.querySelector('.tab-bar').getBoundingClientRect().top),
    };
  })()`);
  assert(initial.hash === '#config-generator', `Unexpected initial hash: ${initial.hash}`);
  assert(initial.active === 'config-generator', `Unexpected initial tab: ${initial.active}`);
  assert(initial.selectedCount === 1, `Expected one selected tab, got ${initial.selectedCount}`);
  assert(Math.abs(initial.tabTop) <= 1, `Deep link did not align tab bar: ${initial.tabTop}`);

  await evaluate(cdp, `document.querySelector('.tab-btn[data-tab="mods"]').click()`);
  await new Promise(resolveWait => setTimeout(resolveWait, 250));
  const afterClick = await evaluate(cdp, `({ hash: location.hash, calls: window.__tabActivations.slice() })`);
  assert(afterClick.hash === '#mods', `Click did not update hash: ${afterClick.hash}`);
  assert(JSON.stringify(afterClick.calls) === JSON.stringify(['mods']), `Unexpected click activations: ${JSON.stringify(afterClick.calls)}`);

  await evaluate(cdp, `new Promise(resolve => { history.back(); setTimeout(resolve, 400); })`);
  const afterBack = await evaluate(cdp, `({
    hash: location.hash,
    calls: window.__tabActivations.slice(),
    active: document.querySelector('.tab-btn.active')?.dataset.tab,
    selectedCount: document.querySelectorAll('[role="tab"][aria-selected="true"]').length
  })`);
  assert(afterBack.hash === '#config-generator', `Back did not restore hash: ${afterBack.hash}`);
  assert(afterBack.active === 'config-generator', `Back did not restore tab: ${afterBack.active}`);
  assert(afterBack.selectedCount === 1, `Back left ${afterBack.selectedCount} selected tabs`);
  assert(
    JSON.stringify(afterBack.calls) === JSON.stringify(['mods', 'config-generator']),
    `Back triggered duplicate/missing activation: ${JSON.stringify(afterBack.calls)}`,
  );

  const numeric = await evaluate(cdp, `(() => {
    const input = document.getElementById('input-MAX_PLAYERS');
    input.value = '';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return {
      stored: values.MAX_PLAYERS,
      input: input.value,
      hasNaN: document.getElementById('output').textContent.includes('NaN')
    };
  })()`);
  assert(numeric.stored === 40 && numeric.input === '40' && !numeric.hasNaN, `Invalid numeric regression: ${JSON.stringify(numeric)}`);

  const negativeDuration = await evaluate(cdp, `(() => {
    const input = document.getElementById('input-KICK_AFK_TIME');
    const before = values.KICK_AFK_TIME;
    input.value = '-1';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return {
      before,
      stored: values.KICK_AFK_TIME,
      input: input.value,
      hint: document.getElementById('th-KICK_AFK_TIME').textContent,
      emitted: document.getElementById('output').textContent.includes('KICK_AFK_TIME=-1')
    };
  })()`);
  assert(
    negativeDuration.stored === negativeDuration.before &&
      negativeDuration.input === String(negativeDuration.before) &&
      negativeDuration.hint === '45 minutes' &&
      !negativeDuration.emitted,
    `Negative duration was accepted: ${JSON.stringify(negativeDuration)}`,
  );

  const secretFileOverride = await evaluate(cdp, `(() => {
    values.ADMIN_PASSWORD = 'must-not-be-emitted';
    values.ADMIN_PASSWORD_FILE = '/run/secrets/conan_admin';
    updateOutput();
    const text = document.getElementById('output').textContent;
    return {
      hasDirectKey: text.includes('ADMIN_PASSWORD='),
      hasDirectValue: text.includes('must-not-be-emitted'),
      hasFileKey: text.includes("ADMIN_PASSWORD_FILE='/run/secrets/conan_admin'")
    };
  })()`);
  assert(
    !secretFileOverride.hasDirectKey && !secretFileOverride.hasDirectValue && secretFileOverride.hasFileKey,
    `Secret-file override emitted an ambiguous direct secret: ${JSON.stringify(secretFileOverride)}`,
  );

  const clipboardCleanup = await evaluate(cdp, `(async () => {
    const existing = new Set(document.querySelectorAll('textarea'));
    const navPrototype = Object.getPrototypeOf(navigator);
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navPrototype, 'clipboard');
    const originalExecCommand = document.execCommand;
    const before = document.querySelectorAll('textarea').length;
    try {
      Object.defineProperty(navPrototype, 'clipboard', { configurable: true, get: () => undefined });
      document.execCommand = () => false;
      const copied = await copyText('clipboard failure QA');
      return { copied, before, after: document.querySelectorAll('textarea').length };
    } finally {
      Object.defineProperty(navPrototype, 'clipboard', clipboardDescriptor);
      document.execCommand = originalExecCommand;
      document.querySelectorAll('textarea').forEach(element => {
        if (!existing.has(element)) element.remove();
      });
    }
  })()`);
  assert(clipboardCleanup.copied === false, 'Clipboard failure test did not exercise the failure path');
  assert(
    clipboardCleanup.after === clipboardCleanup.before,
    `Clipboard fallback leaked a textarea: ${JSON.stringify(clipboardCleanup)}`,
  );

  console.log('Pages browser checks OK: deep link, single activation, Back, numeric/duration guards, secret-file override, clipboard cleanup');
} finally {
  cdp?.close();
  if (chrome && chrome.exitCode === null) chrome.kill('SIGTERM');
  if (staticServer) await new Promise(resolveClose => staticServer.close(resolveClose));
  if (profile) rmSync(profile, { recursive: true, force: true });
}
