#!/usr/bin/env node
// Every tests/*.test.js must be reachable from an npm script, or CI never runs
// it. Six files were orphaned this way, including one added the same week.

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));

const scripts = Object.values(pkg.scripts || {}).join(' ');
const present = fs
  .readdirSync(path.join(root, 'tests'))
  .filter((name) => name.endsWith('.test.js'));

// Playwright specs are selected by config glob rather than by filename.
const playwrightConfigs = fs
  .readdirSync(root)
  .filter((name) => name.startsWith('playwright') && name.endsWith('.config.js'));
const playwrightSelected = playwrightConfigs
  .map((name) => fs.readFileSync(path.join(root, name), 'utf8'))
  .join(' ');

const orphans = present.filter(
  (name) => !scripts.includes(name) && !playwrightSelected.includes(name),
);

if (orphans.length > 0) {
  console.error(
    `${orphans.length} test file(s) are not run by any npm script:\n` +
      orphans.map((name) => `  tests/${name}`).join('\n') +
      '\n\nAdd them to a test:* script so CI executes them.',
  );
  process.exit(1);
}

console.log(`test wiring ok — ${present.length} test files, all reachable from npm scripts`);
