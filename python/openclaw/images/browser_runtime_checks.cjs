#!/usr/bin/env node
'use strict';

function fail(message) {
  console.error(`[browser_runtime_checks][FAIL] ${message}`);
  process.exit(1);
}

function commandOnPath(name) {
  const pathValue = process.env.PATH || '';
  if (!pathValue) {
    return false;
  }
  for (const directory of pathValue.split(':')) {
    if (!directory) {
      continue;
    }
    try {
      const candidate = `${directory.replace(/\/+$/, '')}/${name}`;
      require('node:fs').accessSync(candidate, require('node:fs').constants.X_OK);
      return true;
    } catch (_) {
      // Try the next PATH entry.
    }
  }
  return false;
}

if (!commandOnPath('openclaw')) {
  fail('gateway image must expose openclaw CLI on PATH');
}

let playwrightCore;
try {
  playwrightCore = require('playwright-core');
} catch (error) {
  fail(`gateway image cannot resolve playwright-core from /app: ${error && error.message ? error.message : error}`);
}

if (!playwrightCore.chromium || typeof playwrightCore.chromium.launch !== 'function') {
  fail('playwright-core does not expose a Chromium launcher API');
}

console.log(`[smoke] official gateway node ok: ${process.version}`);
console.log('[smoke] official gateway cli present');
console.log(`[smoke] playwright-core present: ${require.resolve('playwright-core')}`);
console.log('[smoke] playwright chromium launcher API present');
