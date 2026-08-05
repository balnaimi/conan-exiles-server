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
  if (result.exceptionDetails) {
    const details = result.exceptionDetails.exception?.description || result.exceptionDetails.text || 'Browser evaluation failed';
    throw new Error(details);
  }
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

  const unsafeExportReview = await evaluate(cdp, `(() => {
    values.ADMIN_PASSWORD = 'changeme';
    values.ADMIN_PASSWORD_FILE = '';
    renderSections();
    updateOutput();
    openExportReview('download');
    const dialog = document.getElementById('exportReviewDialog');
    const confirm = document.getElementById('confirmExportButton');
    return {
      open: dialog.open,
      disabled: confirm.disabled,
      errors: getConfigurationReview().errors,
      text: dialog.textContent,
    };
  })()`);
  assert(unsafeExportReview.open && unsafeExportReview.disabled && unsafeExportReview.errors.length > 0 && unsafeExportReview.text.includes('Admin password'), `Unsafe export was not blocked by review: ${JSON.stringify(unsafeExportReview)}`);

  const safeExportReview = await evaluate(cdp, `(() => {
    values.ADMIN_PASSWORD = 'a-unique-admin-password';
    updateOutput();
    openExportReview('download');
    return {
      disabled: document.getElementById('confirmExportButton').disabled,
      errors: getConfigurationReview().errors,
      summary: document.getElementById('reviewSummary').textContent,
    };
  })()`);
  assert(!safeExportReview.disabled && safeExportReview.errors.length === 0 && safeExportReview.summary.includes('Ready for Native'), `Safe export review failed: ${JSON.stringify(safeExportReview)}`);

  const referenceReview = await evaluate(cdp, `(() => {
    values.ADMIN_PASSWORD = 'changeme';
    values.ADMIN_PASSWORD_FILE = '';
    openExportReview('example');
    return {
      disabled: document.getElementById('confirmExportButton').disabled,
      summary: document.getElementById('reviewSummary').textContent,
    };
  })()`);
  assert(!referenceReview.disabled && referenceReview.summary.includes('reference'), `Full reference download was incorrectly blocked: ${JSON.stringify(referenceReview)}`);

  const securityEdgeCases = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    values.ADMIN_PASSWORD = 'private-admin-value';
    values.SERVER_PASSWORD = 'private-join-value';
    values.RCON_ENABLED = true;
    values.RCON_PASSWORD = 'private-rcon-value';
    values.ADMIN_PASSWORD_FILE = '   ';
    values.SERVER_PASSWORD_FILE = '\t';
    values.RCON_PASSWORD_FILE = '  ';
    updateOutput();
    const whitespaceReview = getConfigurationReview();
    const whitespaceOutput = document.getElementById('output').textContent;

    importedUnknown = { PLUGIN_TOKEN: 'private-plugin-value' };
    let referenceText = '';
    const originalDownload = download;
    download = (_name, text) => { referenceText = text; };
    performDownloadEnvExample();
    download = originalDownload;

    const complex = "\\\\server\\share\\mods";
    const roundTrip = parseEnvValue(formatEnvValue(complex));
    return {
      whitespaceBlocked: ['ADMIN_PASSWORD_FILE', 'SERVER_PASSWORD_FILE', 'RCON_PASSWORD_FILE'].every(key => whitespaceReview.errors.some(issue => issue.includes(key))),
      whitespaceFilesOmitted: !whitespaceOutput.includes('ADMIN_PASSWORD_FILE=') && !whitespaceOutput.includes('SERVER_PASSWORD_FILE=') && !whitespaceOutput.includes('RCON_PASSWORD_FILE='),
      referenceClean: !referenceText.includes('private-admin-value') && !referenceText.includes('private-join-value') && !referenceText.includes('private-plugin-value') && referenceText.includes("MAX_PLAYERS=40"),
      roundTrip,
      complex,
    };
  })()`);
  assert(securityEdgeCases.whitespaceBlocked && securityEdgeCases.whitespaceFilesOmitted && securityEdgeCases.referenceClean && securityEdgeCases.roundTrip === securityEdgeCases.complex, `Security/reference/round-trip edges failed: ${JSON.stringify(securityEdgeCases)}`);

  const diagnosticFixture = JSON.stringify("SERVER_NAME='First'\nSERVER_NAME='Second'\nSERVER_TYPE=bogus\nMAX_PLAYERS=9999\nNATIVE_BACKUP_ENABLED=maybe\nMALFORMED LINE\nPLUGIN_TOKEN='keep but review'\nSERVER_MOTD='first line\nADMIN_PASSWORD=must-not-apply\nlast line'\n");
  const importDiagnostics = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    const result = importEnvText(${diagnosticFixture});
    openExportReview('download');
    return {
      serverName: values.SERVER_NAME,
      serverType: values.SERVER_TYPE,
      maxPlayers: values.MAX_PLAYERS,
      backupEnabled: values.NATIVE_BACKUP_ENABLED,
      adminPassword: values.ADMIN_PASSWORD,
      imported: result.imported,
      duplicates: result.duplicates,
      malformed: result.malformed,
      errors: result.errors,
      unknown: result.unknown,
      exportDisabled: document.getElementById('confirmExportButton').disabled,
      reviewText: document.getElementById('reviewDetails').textContent,
      hasUnknownAck: Boolean(document.getElementById('acknowledgeUnknownKeys')),
      acknowledgedStillBlocked: (() => {
        values.ADMIN_PASSWORD = 'safe-import-password';
        setValue('SERVER_TYPE', 'pve');
        setValue('MAX_PLAYERS', 40);
        setValue('NATIVE_BACKUP_ENABLED', false);
        setValue('SERVER_MOTD', 'Welcome to the Exiled Lands!');
        setUnknownKeysAcknowledged(true);
        return document.getElementById('confirmExportButton').disabled;
      })(),
    };
  })()`);
  assert(importDiagnostics.serverName === 'Second' && importDiagnostics.serverType === 'pve' && importDiagnostics.maxPlayers === 40 && importDiagnostics.backupEnabled === false && importDiagnostics.adminPassword === 'changeme' && importDiagnostics.imported === 1 && importDiagnostics.duplicates.includes('SERVER_NAME') && importDiagnostics.malformed === 1 && importDiagnostics.errors.some(issue => issue.includes('SERVER_TYPE')) && importDiagnostics.errors.some(issue => issue.includes('MAX_PLAYERS')) && importDiagnostics.errors.some(issue => issue.includes('NATIVE_BACKUP_ENABLED')) && importDiagnostics.errors.some(issue => issue.includes('SERVER_MOTD')) && importDiagnostics.unknown.includes('PLUGIN_TOKEN') && importDiagnostics.exportDisabled && importDiagnostics.reviewText.includes('Imported') && importDiagnostics.hasUnknownAck && importDiagnostics.acknowledgedStillBlocked, `Import diagnostics/safety failed: ${JSON.stringify(importDiagnostics)}`);

  const strictImportSyntax = await evaluate(cdp, `(() => {
    const result = importEnvText("ADMIN_PASSWORD='safe-import-password'\\nSERVER_NAME='foo'junk'\\nRCON_PORT=\\n");
    openExportReview('download');
    return {
      serverName: values.SERVER_NAME,
      rconPort: values.RCON_PORT,
      errors: result.errors,
      blocked: document.getElementById('confirmExportButton').disabled,
    };
  })()`);
  assert(strictImportSyntax.serverName === 'My Conan Server' && strictImportSyntax.rconPort === 25575 && strictImportSyntax.errors.some(issue => issue.includes('SERVER_NAME')) && strictImportSyntax.errors.some(issue => issue.includes('RCON_PORT')) && strictImportSyntax.blocked, `Strict dotenv syntax handling failed: ${JSON.stringify(strictImportSyntax)}`);

  const cleanUnknownAcknowledgement = await evaluate(cdp, `(() => {
    importEnvText("ADMIN_PASSWORD='safe-import-password'\\nPLUGIN_SETTING='review me'\\n");
    openExportReview('download');
    const before = document.getElementById('confirmExportButton').disabled;
    setUnknownKeysAcknowledged(true);
    const after = document.getElementById('confirmExportButton').disabled;
    return { before, after, errors: getConfigurationReview().errors.length };
  })()`);
  assert(cleanUnknownAcknowledgement.before && !cleanUnknownAcknowledgement.after && cleanUnknownAcknowledgement.errors === 0, `Clean unknown-key acknowledgement failed: ${JSON.stringify(cleanUnknownAcknowledgement)}`);

  const importFixture = JSON.stringify("SERVER_NAME='Imported Server'\nMAX_PLAYERS=20\nSERVER_MOTD='It'\\''s ready'\nUNKNOWN_PLUGIN_FLAG='keep me'\n");
  const importedEnv = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    const result = importEnvText(${importFixture});
    return {
      serverName: values.SERVER_NAME,
      maxPlayers: values.MAX_PLAYERS,
      motd: values.SERVER_MOTD,
      imported: result.imported,
      unknown: result.unknown,
      output: buildEnvText('changed'),
    };
  })()`);
  assert(importedEnv.serverName === 'Imported Server' && importedEnv.maxPlayers === 20 && importedEnv.motd === "It's ready" && importedEnv.imported === 3 && importedEnv.unknown.includes('UNKNOWN_PLUGIN_FLAG') && importedEnv.output.includes("UNKNOWN_PLUGIN_FLAG='keep me'"), `Existing .env import failed: ${JSON.stringify(importedEnv)}`);

  const repeatedImport = await evaluate(cdp, `(() => {
    values.MAX_PLAYERS = 60;
    importedUnknown.STALE_TOKEN = 'remove-me';
    const result = importEnvText("SERVER_NAME='Fresh Import'\\n");
    return {
      serverName: values.SERVER_NAME,
      maxPlayers: values.MAX_PLAYERS,
      unknownKeys: Object.keys(importedUnknown),
      imported: result.imported,
    };
  })()`);
  assert(repeatedImport.serverName === 'Fresh Import' && repeatedImport.maxPlayers === 40 && repeatedImport.unknownKeys.length === 0 && repeatedImport.imported === 1, `Successive import retained stale state: ${JSON.stringify(repeatedImport)}`);

  const quickMode = await evaluate(cdp, `(() => {
    setGeneratorView('quick');
    const visibleKeys = [...document.querySelectorAll('#sections .field:not(.filtered-out)')].map(field => field.id.replace('field-', ''));
    const quickCount = visibleKeys.length;
    setGeneratorView('all');
    const allCount = document.querySelectorAll('#sections .field:not(.filtered-out)').length;
    const totalCount = document.querySelectorAll('#sections .field').length;
    return {
      quickCount,
      allCount,
      totalCount,
      hasEssentials: ['SERVER_NAME', 'ADMIN_PASSWORD', 'SERVER_PORT', 'SERVER_MOD_LIST', 'NATIVE_BACKUP_ENABLED'].every(key => visibleKeys.includes(key)),
      pressed: document.getElementById('viewModeAll').getAttribute('aria-pressed'),
    };
  })()`);
  assert(quickMode.quickCount > 0 && quickMode.quickCount < 50 && quickMode.allCount === quickMode.totalCount && quickMode.totalCount === 250 && quickMode.hasEssentials && quickMode.pressed === 'true', `Quick/all generator modes failed: ${JSON.stringify(quickMode)}`);

  const presetFlow = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    const before = values.SERVER_TYPE;
    previewPreset('native-pvp');
    const preview = {
      open: document.getElementById('presetDialog').open,
      unchanged: values.SERVER_TYPE === before,
      text: document.getElementById('presetChanges').textContent,
    };
    confirmPreset();
    return {
      preview,
      type: values.SERVER_TYPE,
      backups: values.NATIVE_BACKUP_ENABLED,
    };
  })()`);
  assert(presetFlow.preview.open && presetFlow.preview.unchanged && /\d+ changes?/.test(presetFlow.preview.text) && presetFlow.preview.text.includes('SERVER_TYPE') && presetFlow.preview.text.includes('NATIVE_BACKUP_ENABLED') && presetFlow.type === 'pvp' && presetFlow.backups === true, `Transparent preset flow failed: ${JSON.stringify(presetFlow)}`);

  const draftPrivacy = await evaluate(cdp, `(() => {
    localStorage.clear();
    resetToDefaults(false);
    values.SERVER_NAME = 'Saved locally';
    values.ADMIN_PASSWORD = 'must-never-enter-local-storage';
    saveDraft();
    const raw = localStorage.getItem(DRAFT_STORAGE_KEY) || '';
    values.SERVER_NAME = 'Changed after draft';
    values.MAX_PLAYERS = 55;
    values.ADMIN_PASSWORD = 'keep-current-password';
    restoreDraft();
    return {
      rawHasSecret: raw.includes('must-never-enter-local-storage'),
      serverName: values.SERVER_NAME,
      maxPlayers: values.MAX_PLAYERS,
      adminPassword: values.ADMIN_PASSWORD,
      status: document.getElementById('draftStatus').textContent,
    };
  })()`);
  assert(!draftPrivacy.rawHasSecret && draftPrivacy.serverName === 'Saved locally' && draftPrivacy.maxPlayers === 40 && draftPrivacy.adminPassword === 'keep-current-password' && draftPrivacy.status.includes('restored'), `Local draft privacy/restore failed: ${JSON.stringify(draftPrivacy)}`);

  const draftSecurity = await evaluate(cdp, `(() => {
    localStorage.clear();
    resetToDefaults(false);
    const payload = '40\\"><img id=\\"draft-xss\\" src=\\"x\\" onerror=\\"window.__draftXss=1\\">';
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ savedAt: new Date().toISOString(), values: { MAX_PLAYERS: payload, SERVER_TYPE: 'bogus', NATIVE_BACKUP_ENABLED: 'true' } }));
    window.__draftXss = 0;
    const restored = restoreDraft();
    const invalidRemoved = localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    const draftInjected = Boolean(document.getElementById('draft-xss'));
    values.MAX_PLAYERS = payload;
    renderSections();
    const renderInjected = Boolean(document.getElementById('draft-xss'));
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ values: { ADMIN_PASSWORD: 'forbidden' } }));
    const passwordRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ values: { UNKNOWN_TOKEN: 'forbidden' } }));
    const unknownRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    localStorage.setItem(DRAFT_STORAGE_KEY, '{malformed');
    const malformedRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ savedAt: new Date().toISOString(), values: { SERVER_NAME: 'safe' }, ADMIN_PASSWORD: 'forbidden' }));
    const rootPasswordRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ savedAt: new Date().toISOString(), values: { SERVER_NAME: 'safe' }, UNKNOWN_TOKEN: 'forbidden' }));
    const rootUnknownRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ savedAt: new Date().toISOString(), values: { SERVER_REGION: 0 } }));
    const wrongSelectTypeRejected = !restoreDraft() && localStorage.getItem(DRAFT_STORAGE_KEY) === null;
    resetToDefaults(false);
    return { restored, invalidRemoved, passwordRejected, unknownRejected, malformedRejected, rootPasswordRejected, rootUnknownRejected, wrongSelectTypeRejected, draftInjected, renderInjected, executed: window.__draftXss, maxPlayers: values.MAX_PLAYERS };
  })()`);
  assert(!draftSecurity.restored && draftSecurity.invalidRemoved && draftSecurity.passwordRejected && draftSecurity.unknownRejected && draftSecurity.malformedRejected && draftSecurity.rootPasswordRejected && draftSecurity.rootUnknownRejected && draftSecurity.wrongSelectTypeRejected && !draftSecurity.draftInjected && !draftSecurity.renderInjected && draftSecurity.executed === 0 && draftSecurity.maxPlayers === 40, `Draft schema/XSS protection failed: ${JSON.stringify(draftSecurity)}`);

  const conditionalFields = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    setGeneratorView('all');
    filterSettings();
    const initial = {
      pvpVisible: !document.getElementById('field-PVP_TIME_MON_START').classList.contains('filtered-out'),
      rconVisible: !document.getElementById('field-RCON_PASSWORD').classList.contains('filtered-out'),
      backupVisible: !document.getElementById('field-NATIVE_BACKUP_MODE').classList.contains('filtered-out'),
      pvpDisabled: document.getElementById('input-PVP_TIME_MON_START').disabled,
      rconDisabled: document.getElementById('input-RCON_PASSWORD').disabled,
      backupDisabled: document.getElementById('input-NATIVE_BACKUP_MODE').disabled,
      note: document.querySelector('#field-PVP_TIME_MON_START .conditional-note')?.textContent,
    };
    setValue('SERVER_TYPE', 'pve-c');
    setValue('RCON_ENABLED', true);
    setValue('NATIVE_BACKUP_ENABLED', true);
    return {
      initial,
      pvp: !document.getElementById('field-PVP_TIME_MON_START').classList.contains('filtered-out') && !document.getElementById('input-PVP_TIME_MON_START').disabled,
      rcon: !document.getElementById('field-RCON_PASSWORD').classList.contains('filtered-out') && !document.getElementById('input-RCON_PASSWORD').disabled,
      backup: !document.getElementById('field-NATIVE_BACKUP_MODE').classList.contains('filtered-out') && !document.getElementById('input-NATIVE_BACKUP_MODE').disabled,
    };
  })()`);
  assert(conditionalFields.initial.pvpVisible && conditionalFields.initial.rconVisible && conditionalFields.initial.backupVisible && conditionalFields.initial.pvpDisabled && conditionalFields.initial.rconDisabled && conditionalFields.initial.backupDisabled && conditionalFields.initial.note.includes('Inactive until') && conditionalFields.pvp && conditionalFields.rcon && conditionalFields.backup, `Conditional field disclosure failed: ${JSON.stringify(conditionalFields)}`);

  const statusFilters = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    setGeneratorView('all');
    const select = document.getElementById('settingStatusFilter');
    select.value = 'needs-verification';
    filterSettings();
    const needs = {
      sprint: !document.getElementById('field-PLAYER_SPRINT_SPEED').classList.contains('filtered-out'),
      harvest: document.getElementById('field-HARVEST_AMOUNT').classList.contains('filtered-out'),
      badge: document.querySelector('#field-PLAYER_SPRINT_SPEED .status-badge')?.textContent,
    };
    select.value = 'native';
    filterSettings();
    const native = !document.getElementById('field-NATIVE_VALIDATE_SERVER').classList.contains('filtered-out');
    select.value = 'official';
    filterSettings();
    const official = !document.getElementById('field-HARVEST_AMOUNT').classList.contains('filtered-out');
    select.value = 'all';
    filterSettings();
    return { needs, native, official };
  })()`);
  assert(statusFilters.needs.sprint && statusFilters.needs.harvest && statusFilters.needs.badge.includes('Needs verification') && statusFilters.native && statusFilters.official, `Status filters/badges failed: ${JSON.stringify(statusFilters)}`);

  const fieldUtilities = await evaluate(cdp, `(() => {
    setGeneratorView('all');
    document.getElementById('settingStatusFilter').value = 'all';
    setValue('HARVEST_AMOUNT', 2);
    let copied = '';
    const originalCopyText = copyText;
    copyText = async text => { copied = text; return true; };
    copyEnvKey('HARVEST_AMOUNT');
    copyText = originalCopyText;
    openSettingDeepLink('HARVEST_AMOUNT', true, false);
    setDetectedTimezone('Asia/Qatar');
    return {
      copied,
      hash: location.hash,
      change: document.getElementById('change-HARVEST_AMOUNT').textContent,
      targetVisible: !document.getElementById('field-HARVEST_AMOUNT').classList.contains('filtered-out'),
      timezone: values.TZ,
      timezoneList: document.getElementById('input-TZ').getAttribute('list'),
      timezoneCount: document.querySelectorAll('#timezoneOptions option').length,
      hasQatar: Boolean(document.querySelector('#timezoneOptions option[value="Asia/Qatar"]')),
    };
  })()`);
  assert(fieldUtilities.copied === 'HARVEST_AMOUNT' && fieldUtilities.hash === '#setting=HARVEST_AMOUNT' && fieldUtilities.change.includes('Default') && fieldUtilities.change.includes('2') && fieldUtilities.targetVisible && fieldUtilities.timezone === 'Asia/Qatar' && fieldUtilities.timezoneList === 'timezoneOptions' && fieldUtilities.timezoneCount > 100 && fieldUtilities.hasQatar, `Field utilities/deep-link/timezone failed: ${JSON.stringify(fieldUtilities)}`);

  const timezoneFallback = await evaluate(cdp, `(() => {
    const descriptor = Object.getOwnPropertyDescriptor(Intl, 'supportedValuesOf');
    Object.defineProperty(Intl, 'supportedValuesOf', { configurable: true, value: undefined });
    populateTimezoneOptions();
    const count = document.querySelectorAll('#timezoneOptions option').length;
    const hasQatar = Boolean(document.querySelector('#timezoneOptions option[value="Asia/Qatar"]'));
    if (descriptor) Object.defineProperty(Intl, 'supportedValuesOf', descriptor);
    else delete Intl.supportedValuesOf;
    populateTimezoneOptions();
    return { count, hasQatar, fallbackSize: IANA_TIMEZONE_FALLBACK.length };
  })()`);
  assert(timezoneFallback.count > 500 && timezoneFallback.fallbackSize > 500 && timezoneFallback.hasQatar, `IANA timezone fallback is incomplete: ${JSON.stringify(timezoneFallback)}`);

  await evaluate(cdp, `history.back()`);
  await eventuallyEvaluate(cdp, `location.hash`, value => value !== '#setting=HARVEST_AMOUNT', 'setting deep-link browser Back');
  const backState = await evaluate(cdp, `({ hash: location.hash, search: document.getElementById('settingSearch').value, view: generatorView, changedOnly: document.getElementById('changedOnlyFilter').checked, status: document.getElementById('settingStatusFilter').value })`);
  assert(backState.hash !== '#setting=HARVEST_AMOUNT' && backState.search === '' && backState.view === 'quick' && !backState.changedOnly && backState.status === 'all', `Setting deep-link Back left stale filters: ${JSON.stringify(backState)}`);
  await evaluate(cdp, `history.forward()`);
  const forwardSetting = await eventuallyEvaluate(cdp, `({ hash: location.hash, focused: document.activeElement?.id })`, value => value?.hash === '#setting=HARVEST_AMOUNT', 'setting deep-link browser Forward');
  assert(forwardSetting.hash === '#setting=HARVEST_AMOUNT', `Setting deep-link Forward failed: ${JSON.stringify(forwardSetting)}`);

  const malformedSettingHash = await evaluate(cdp, `(() => {
    history.pushState({}, '', '#setting=%E0%A4%A');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    return { active: document.querySelector('.tab-btn.active')?.dataset.tab, decoded: decodeSettingKey('setting=%E0%A4%A') };
  })()`);
  assert(malformedSettingHash.active === 'config-generator' && malformedSettingHash.decoded === '', `Malformed setting hash was not handled safely: ${JSON.stringify(malformedSettingHash)}`);

  const reviewAndNextSteps = await evaluate(cdp, `(async () => {
    values.ADMIN_PASSWORD = 'safe-review-password';
    values.SERVER_NAME = 'Reviewed Server';
    importedUnknown = {};
    unknownKeysAcknowledged = false;
    renderSections();
    updateOutput();
    openExportReview('copy');
    const reviewText = document.getElementById('reviewDetails').textContent;
    const reviewLinks = document.querySelectorAll('#reviewDetails [data-review-setting]').length;
    const originalPerformCopy = performCopyOutput;
    performCopyOutput = async () => true;
    await confirmExport();
    performCopyOutput = originalPerformCopy;
    const next = document.getElementById('generatorNextSteps');
    return {
      reviewText,
      reviewLinks,
      nextHidden: next.hidden,
      nextText: next.textContent,
    };
  })()`);
  assert(reviewAndNextSteps.reviewText.includes('Server Info') && reviewAndNextSteps.reviewText.includes('Restart/recreate required') && reviewAndNextSteps.reviewLinks > 0 && !reviewAndNextSteps.nextHidden && reviewAndNextSteps.nextText.includes('docker-compose.native.yml') && reviewAndNextSteps.nextText.includes('Existing Wine'), `Review details/next steps failed: ${JSON.stringify(reviewAndNextSteps)}`);

  const failedCopy = await evaluate(cdp, `(async () => {
    document.getElementById('generatorNextSteps').hidden = true;
    resetToDefaults(false);
    values.ADMIN_PASSWORD = 'safe-copy-password';
    openExportReview('copy');
    const originalPerformCopy = performCopyOutput;
    performCopyOutput = async () => false;
    await confirmExport();
    performCopyOutput = originalPerformCopy;
    return {
      dialogOpen: document.getElementById('exportReviewDialog').open,
      nextHidden: document.getElementById('generatorNextSteps').hidden,
    };
  })()`);
  assert(failedCopy.dialogOpen && failedCopy.nextHidden, `Failed clipboard export incorrectly showed success: ${JSON.stringify(failedCopy)}`);

  const reviewNavigation = await evaluate(cdp, `(() => {
    document.getElementById('exportReviewDialog').close();
    resetToDefaults(false);
    openExportReview('download');
    const link = document.querySelector('#reviewDetails [data-review-setting]');
    const key = link?.dataset.reviewSetting || '';
    link?.click();
    return {
      linkFound: Boolean(link),
      key,
      dialogClosed: !document.getElementById('exportReviewDialog').open,
      hash: location.hash,
      visible: key ? !document.getElementById('field-' + key).classList.contains('filtered-out') : false,
    };
  })()`);
  assert(reviewNavigation.linkFound && reviewNavigation.dialogClosed && reviewNavigation.hash === `#setting=${reviewNavigation.key}` && reviewNavigation.visible, `Review finding navigation failed: ${JSON.stringify(reviewNavigation)}`);

  const sectionOrder = await evaluate(cdp, `({ ids: CONFIG.slice(0, 6).map(section => section.id), hasOld: CONFIG.some(section => section.id === 'nativeops') })`);
  assert(JSON.stringify(sectionOrder.ids) === JSON.stringify(['server', 'admin', 'network', 'native-runtime', 'native-backups', 'native-secrets']) && !sectionOrder.hasOld, `Native sections are not promoted/split: ${JSON.stringify(sectionOrder)}`);

  const undoFlow = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    clearUndoHistory();
    setValue('SERVER_NAME', 'Undo me');
    const changed = values.SERVER_NAME;
    undoLastChange();
    const afterChangeUndo = values.SERVER_NAME;
    setValue('SERVER_NAME', 'Before reset');
    values.ADMIN_PASSWORD = 'reset-private-password';
    importedUnknown = { PLUGIN_TOKEN: 'reset-private-token' };
    performReset();
    const resetValue = values.SERVER_NAME;
    const resetPassword = values.ADMIN_PASSWORD;
    const resetUnknownCount = Object.keys(importedUnknown).length;
    const resetUndoResult = undoLastChange();
    return { changed, afterChangeUndo, resetValue, resetPassword, resetUnknownCount, resetUndoResult, historyLength: undoHistory.length, toast: document.getElementById('toast').textContent };
  })()`);
  assert(undoFlow.changed === 'Undo me' && undoFlow.afterChangeUndo === 'My Conan Server' && undoFlow.resetValue === 'My Conan Server' && undoFlow.resetPassword === 'changeme' && undoFlow.resetUnknownCount === 0 && !undoFlow.resetUndoResult && undoFlow.historyLength === 0 && undoFlow.toast.includes('undo history were cleared'), `Undo/reset boundary failed: ${JSON.stringify(undoFlow)}`);

  const undoPrivacy = await evaluate(cdp, `(() => {
    resetToDefaults(false);
    clearUndoHistory();
    values.ADMIN_PASSWORD = 'undo-private-password';
    importedUnknown = { PLUGIN_TOKEN: 'undo-private-token' };
    captureUndo('privacy check');
    const serialized = JSON.stringify(undoHistory);
    return { hasPassword: serialized.includes('undo-private-password'), hasUnknownSecret: serialized.includes('undo-private-token') };
  })()`);
  assert(!undoPrivacy.hasPassword && !undoPrivacy.hasUnknownSecret, `Undo history retained secrets: ${JSON.stringify(undoPrivacy)}`);

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

  await navigate(cdp, `${base}/config/#config-generator`);
  await waitForPage(cdp, '/config/');
  const mobileGenerator = await evaluate(cdp, `(() => {
    previewPreset('native-pvp');
    const presetOverflow = document.documentElement.scrollWidth > document.documentElement.clientWidth;
    document.getElementById('presetDialog').close();
    openExportReview('download');
    const reviewOverflow = document.documentElement.scrollWidth > document.documentElement.clientWidth;
    return {
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      presetOverflow,
      reviewOverflow,
      toolbarWidth: document.querySelector('.generator-toolbar').getBoundingClientRect().right,
      viewport: document.documentElement.clientWidth,
      releaseText: document.getElementById('tab-about').textContent,
    };
  })()`);
  assert(!mobileGenerator.pageOverflow && !mobileGenerator.presetOverflow && !mobileGenerator.reviewOverflow && mobileGenerator.toolbarWidth <= mobileGenerator.viewport && mobileGenerator.releaseText.includes('v2.9.0') && mobileGenerator.releaseText.includes('Native Linux Runtime'), `Mobile generator/release copy failed: ${JSON.stringify(mobileGenerator)}`);
  await cdp.send('Emulation.clearDeviceMetricsOverride');

  assert(browserErrors.length === 0, `Browser console/JavaScript errors: ${JSON.stringify(browserErrors)}`);
  console.log('Pages browser checks OK: multi-page routes, legacy redirects, Native anchor, generator safety, docs/migration, and 390x844 overflow');
} finally {
  cdp?.close();
  await stopChild(chrome);
  if (staticServer) await new Promise(resolveClose => staticServer.close(resolveClose));
  if (profile) rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
