#!/usr/bin/env node
/** Browser-level regression checks for the multi-page GitHub Pages site. */

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
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
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
    const found = spawnSync('sh', ['-c', 'command -v "$1"', 'sh', candidate], { encoding: 'utf8' });
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
    let relative = pathname.replace(/^\/+/, '');
    if (!relative || pathname.endsWith('/')) relative += 'index.html';
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

async function waitForTarget(port, expectedUrl, child, stderr, attempts = 250) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`Chromium exited before CDP was ready: ${stderr()}`);
    }
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = targets.find(target => target.type === 'page' && target.url.startsWith(expectedUrl));
      if (page?.webSocketDebuggerUrl) return page;
    } catch {
      // Chromium may still be starting.
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 100));
  }
  throw new Error(`Timed out after 25s waiting for Chromium DevTools target: ${stderr()}`);
}

function createCdp(webSocketUrl, browserErrors) {
  const socket = new WebSocket(webSocketUrl);
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id) {
      if (message.method === 'Runtime.exceptionThrown') {
        browserErrors.push(message.params?.exceptionDetails?.text || 'Uncaught browser exception');
      }
      if (message.method === 'Log.entryAdded' && message.params?.entry?.level === 'error') {
        browserErrors.push(message.params.entry.text);
      }
      return;
    }
    if (!pending.has(message.id)) return;
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
    close() { socket.close(); },
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

function isTransientNavigationError(error) {
  return /Execution context was destroyed|Cannot find context|Inspected target navigated or closed/i.test(error.message);
}

async function eventuallyEvaluate(cdp, expression, predicate, description, attempts = 100) {
  let lastValue;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      lastValue = await evaluate(cdp, expression);
      if (predicate(lastValue)) return lastValue;
    } catch (error) {
      if (!isTransientNavigationError(error)) throw error;
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 100));
  }
  throw new Error(`Timed out waiting for ${description}: ${JSON.stringify(lastValue)}`);
}

async function navigate(cdp, url) {
  try {
    await cdp.send('Page.navigate', { url });
  } catch (error) {
    if (!isTransientNavigationError(error)) throw error;
  }
}

async function waitForPage(cdp, pathname, hash = null) {
  return eventuallyEvaluate(cdp, `({
    readyState: document.readyState,
    pathname: location.pathname,
    hash: location.hash,
    title: document.title
  })`, value => value?.readyState === 'complete' && value.pathname === pathname &&
    (hash === null || value.hash === hash), `${pathname}${hash || ''}`);
}

function waitForChildExit(child, timeoutMs) {
  return new Promise(resolveWait => {
    if (child.exitCode !== null || child.signalCode !== null) return resolveWait(true);
    const timer = setTimeout(() => {
      child.off('exit', onExit);
      resolveWait(false);
    }, timeoutMs);
    const onExit = () => {
      clearTimeout(timer);
      resolveWait(true);
    };
    child.once('exit', onExit);
  });
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  if (await waitForChildExit(child, 5000)) return;
  child.kill('SIGKILL');
  await waitForChildExit(child, 2000);
}

let staticServer;
let chrome;
let profile;
let cdp;
let chromeStderr = '';
const browserErrors = [];
try {
  staticServer = await startStaticServer();
  const pagePort = staticServer.address().port;
  const base = `http://127.0.0.1:${pagePort}`;
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
    `${base}/`,
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.setEncoding('utf8');
  chrome.stderr.on('data', chunk => { chromeStderr = `${chromeStderr}${chunk}`.slice(-8000); });

  const target = await waitForTarget(debugPort, `${base}/`, chrome, () => chromeStderr);
  cdp = createCdp(target.webSocketDebuggerUrl, browserErrors);
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await waitForPage(cdp, '/');

  const landing = await evaluate(cdp, `(() => {
    const root = document.documentElement;
    const nav = [...document.querySelectorAll('.top-nav a')].map(link => link.getAttribute('href'));
    return {
      h1: document.querySelector('h1')?.textContent.trim(),
      current: document.querySelector('.top-nav [aria-current="page"]')?.textContent.trim(),
      nav,
      native: Boolean(document.getElementById('native-quick-start')),
      generatorDataPresent: document.documentElement.textContent.includes('MAX_PLAYERS') && Boolean(window.CONFIG),
      overflow: root.scrollWidth > root.clientWidth,
      stylesheets: [...document.styleSheets].length,
    };
  })()`);
  assert(landing.h1 === 'Run your server without reading a manual.', `Unexpected landing h1: ${JSON.stringify(landing)}`);
  assert(landing.current === 'Quick Start' && landing.nav.includes('config/') && landing.nav.includes('docs/') && landing.nav.includes('migrate/'), `Landing navigation failed: ${JSON.stringify(landing)}`);
  assert(landing.native && !landing.generatorDataPresent && !landing.overflow && landing.stylesheets >= 1, `Landing contract failed: ${JSON.stringify(landing)}`);

  await evaluate(cdp, `document.querySelector('a[href="#native-quick-start"]').click()`);
  const nativeAnchor = await eventuallyEvaluate(cdp, `(() => {
    const target = document.getElementById('native-quick-start');
    const header = document.querySelector('.site-header');
    const rect = target.getBoundingClientRect();
    return { hash: location.hash, top: rect.top, headerBottom: header.getBoundingClientRect().bottom };
  })()`, value => value?.hash === '#native-quick-start', 'Native Quick Start anchor');
  assert(nativeAnchor.top >= nativeAnchor.headerBottom - 1, `Native anchor is hidden by sticky header: ${JSON.stringify(nativeAnchor)}`);

  await navigate(cdp, `${base}/index.html#quick-start`);
  const legacyQuickStart = await eventuallyEvaluate(cdp, `(() => {
    const target = document.getElementById('native-quick-start');
    const header = document.querySelector('.site-header');
    if (!target || !header) return null;
    const rect = target.getBoundingClientRect();
    return { hash: location.hash, top: rect.top, headerBottom: header.getBoundingClientRect().bottom };
  })()`, value => value?.hash === '#native-quick-start', 'Legacy Quick Start anchor');
  assert(legacyQuickStart.top >= legacyQuickStart.headerBottom - 1, `Legacy Quick Start anchor is hidden by sticky header: ${JSON.stringify(legacyQuickStart)}`);

  await navigate(cdp, `${base}/index.html#config-generator`);
  await waitForPage(cdp, '/config/');
  const legacyConfig = await evaluate(cdp, `({ replaced: history.length, title: document.title })`);
  assert(legacyConfig.title.startsWith('Configuration Generator'), `Legacy generator redirect failed: ${JSON.stringify(legacyConfig)}`);

  const generator = await evaluate(cdp, `(() => ({
    active: document.querySelector('.tab-btn.active')?.dataset.tab,
    visibleTabs: [...document.querySelectorAll('.tab-btn')].filter(tab => getComputedStyle(tab).display !== 'none').map(tab => tab.dataset.tab),
    panelHidden: document.getElementById('tab-config-generator').hidden,
    input: Boolean(document.getElementById('input-MAX_PLAYERS')),
    sections: document.querySelectorAll('#sections .section').length,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }))()`);
  assert(generator.active === 'config-generator' && JSON.stringify(generator.visibleTabs) === JSON.stringify(['config-generator']) && !generator.panelHidden && generator.input && generator.sections > 10 && !generator.overflow, `Generator route failed: ${JSON.stringify(generator)}`);

  const numeric = await evaluate(cdp, `(() => {
    const input = document.getElementById('input-MAX_PLAYERS');
    input.value = '';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return { stored: values.MAX_PLAYERS, input: input.value, hasNaN: document.getElementById('output').textContent.includes('NaN') };
  })()`);
  assert(numeric.stored === 40 && numeric.input === '40' && !numeric.hasNaN, `Invalid numeric regression: ${JSON.stringify(numeric)}`);

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
  assert(!secretFileOverride.hasDirectKey && !secretFileOverride.hasDirectValue && secretFileOverride.hasFileKey, `Secret-file override regression: ${JSON.stringify(secretFileOverride)}`);

  await navigate(cdp, `${base}/index.html#cpu-compatibility`);
  await waitForPage(cdp, '/docs/operations/', '#cpu-compatibility-check');
  const legacyCpu = await eventuallyEvaluate(cdp, `(() => {
    const target = document.getElementById('cpu-compatibility-check');
    const rect = target?.getBoundingClientRect();
    const headerBottom = document.querySelector('.site-header')?.getBoundingClientRect().bottom ?? 0;
    return {
      target: Boolean(target),
      visible: Boolean(rect && rect.bottom > headerBottom && rect.top < innerHeight),
      top: rect?.top ?? null,
      headerBottom,
      sidebar: Boolean(document.querySelector('.docs-sidebar')),
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  })()`, value => value?.target && value.visible && value.top >= value.headerBottom && !value.overflow,
  'visible legacy CPU anchor below the sticky header');
  assert(legacyCpu.target && legacyCpu.visible && legacyCpu.top >= legacyCpu.headerBottom && legacyCpu.sidebar && !legacyCpu.overflow, `Legacy CPU redirect failed: ${JSON.stringify(legacyCpu)}`);

  await navigate(cdp, `${base}/migrate/`);
  await waitForPage(cdp, '/migrate/');
  const migration = await evaluate(cdp, `(() => ({
    h1: document.querySelector('h1')?.textContent.trim(),
    dryRun: Boolean(document.getElementById('dry-run')),
    rollback: Boolean(document.getElementById('rollback')),
    warning: document.body.textContent.includes('Never point Native at Wine'),
    dangerous: document.body.textContent.includes('down -v') && !document.body.textContent.includes('Never use'),
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }))()`);
  assert(migration.h1 === 'Wine to Native migration' && migration.dryRun && migration.rollback && migration.warning && !migration.dangerous && !migration.overflow, `Migration route failed: ${JSON.stringify(migration)}`);

  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await navigate(cdp, `${base}/`);
  await waitForPage(cdp, '/');
  const mobileLanding = await evaluate(cdp, `({
    width: innerWidth,
    height: innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    routeRight: Math.max(...[...document.querySelectorAll('.route-card')].map(card => card.getBoundingClientRect().right)),
  })`);
  assert(mobileLanding.width === 390 && mobileLanding.height === 844 && mobileLanding.scrollWidth <= 390 && mobileLanding.bodyScrollWidth <= 390 && mobileLanding.routeRight <= 390, `Mobile landing overflow: ${JSON.stringify(mobileLanding)}`);

  await navigate(cdp, `${base}/docs/operations/`);
  await waitForPage(cdp, '/docs/operations/');
  const mobileDocs = await evaluate(cdp, `({
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    sidebarStatic: getComputedStyle(document.querySelector('.docs-sidebar')).position,
  })`);
  assert(mobileDocs.scrollWidth <= 390 && mobileDocs.bodyScrollWidth <= 390 && mobileDocs.sidebarStatic === 'static', `Mobile docs overflow/layout failed: ${JSON.stringify(mobileDocs)}`);
  await cdp.send('Emulation.clearDeviceMetricsOverride');

  assert(browserErrors.length === 0, `Browser console/JavaScript errors: ${JSON.stringify(browserErrors)}`);
  console.log('Pages browser checks OK: multi-page routes, legacy redirects, Native anchor, generator safety, docs/migration, and 390x844 overflow');
} finally {
  cdp?.close();
  await stopChild(chrome);
  if (staticServer) await new Promise(resolveClose => staticServer.close(resolveClose));
  if (profile) rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
