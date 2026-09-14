#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..', '..');

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

const primitiveSchemasPath = firstExisting([
  path.join(repoRoot, 'chat-ui', 'src', 'ui', 'page-renderer', 'primitive_schemas.json'),
  path.join(repoRoot, 'mozaiks_chat_ui', 'src', 'ui', 'page-renderer', 'primitive_schemas.json'),
]);

const primitiveBarrelPath = firstExisting([
  path.join(repoRoot, 'chat-ui', 'src', 'ui', 'primitives', 'index.js'),
  path.join(repoRoot, 'mozaiks_chat_ui', 'src', 'ui', 'primitives', 'index.js'),
]);

const pageSchemaRoots = [
  path.join(repoRoot, 'factory_app', 'app', 'ui', 'pages'),
  path.join(repoRoot, 'web_shell', 'playwright', 'fixtures', 'generated-app', 'app', 'ui', 'pages'),
  path.join(repoRoot, 'generated'),
];

const reactSurfaceRoots = [
  path.join(repoRoot, 'factory_app', 'app', 'ui'),
  path.join(repoRoot, 'factory_app', 'workflows'),
  path.join(repoRoot, 'web_shell', 'playwright', 'fixtures', 'generated-app', 'app', 'ui'),
  path.join(repoRoot, 'generated'),
];

const localPrimitiveCatalogRoots = [
  path.join(repoRoot, 'factory_app', 'app', 'ui'),
  path.join(repoRoot, 'web_shell', 'playwright', 'fixtures', 'generated-app', 'app', 'ui'),
  path.join(repoRoot, 'generated'),
];

const IGNORED_DIRS = new Set([
  '.git',
  '.venv',
  'node_modules',
  '__pycache__',
  'dist',
  'build',
]);

function relative(filePath) {
  return path.relative(repoRoot, filePath).replace(/\\/g, '/');
}

function exists(filePath) {
  return fs.existsSync(filePath);
}

function walk(root, extensions) {
  if (!exists(root)) return [];

  const files = [];
  const stack = [root];

  while (stack.length > 0) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });

    for (const entry of entries) {
      if (IGNORED_DIRS.has(entry.name)) continue;

      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }

      if (extensions.has(path.extname(entry.name).toLowerCase())) {
        files.push(fullPath);
      }
    }
  }

  return files.sort();
}

function addFailure(failures, filePath, message) {
  failures.push(`${relative(filePath)}: ${message}`);
}

function validatePagePrimitives(failures) {
  if (!exists(primitiveSchemasPath)) {
    failures.push(`primitive schema catalog not found at ${primitiveSchemasPath}`);
    return;
  }

  const primitiveSchemas = JSON.parse(fs.readFileSync(primitiveSchemasPath, 'utf8'));
  const allowedPrimitives = new Set(
    Object.keys(primitiveSchemas).filter((key) => !key.startsWith('_'))
  );
  const yamlFiles = pageSchemaRoots.flatMap((root) => walk(root, new Set(['.yaml', '.yml'])));
  const primitivePattern = /^\s*primitive:\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:#.*)?$/gm;

  for (const filePath of yamlFiles) {
    const source = fs.readFileSync(filePath, 'utf8');
    let match;

    while ((match = primitivePattern.exec(source)) !== null) {
      const primitiveName = match[1];
      if (!allowedPrimitives.has(primitiveName)) {
        addFailure(
          failures,
          filePath,
          `unknown page primitive "${primitiveName}". Update PrimitiveRegistry/Schemas/Catalog and export primitive_schemas.json.`
        );
      }
    }
  }
}

function validateReactImports(failures) {
  const reactFiles = reactSurfaceRoots.flatMap((root) => (
    walk(root, new Set(['.js', '.jsx', '.ts', '.tsx']))
  ));
  const forbiddenPatterns = [
    {
      pattern: /@mozaiks\/chat-ui\/src/g,
      message: 'imports chat-ui internals; use the public @mozaiks/chat-ui/ui entrypoint.',
    },
    {
      pattern: /@mozaiks\/chat-ui\/ui\/primitives/g,
      message: 'deep-imports primitive internals; use the public @mozaiks/chat-ui/ui entrypoint.',
    },
    {
      pattern: /chat-ui[\\/]+src[\\/]+ui[\\/]+primitives/g,
      message: 'imports primitive source files directly; use the public @mozaiks/chat-ui/ui entrypoint.',
    },
    {
      pattern: /StudioPrimitives/g,
      message: 'references a factory-local primitive catalog; shared primitives belong in chat-ui/src/ui/primitives.',
    },
  ];

  for (const filePath of reactFiles) {
    const source = fs.readFileSync(filePath, 'utf8');
    for (const { pattern, message } of forbiddenPatterns) {
      if (pattern.test(source)) {
        addFailure(failures, filePath, message);
      }
      pattern.lastIndex = 0;
    }
  }
}

function validateNoLocalPrimitiveCatalogs(failures) {
  const localPrimitiveFiles = localPrimitiveCatalogRoots.flatMap((root) => (
    walk(root, new Set(['.js', '.jsx', '.ts', '.tsx']))
      .filter((filePath) => /(^|[\\/])[^\\/]*Primitives\.(js|jsx|ts|tsx)$/.test(filePath))
  ));

  for (const filePath of localPrimitiveFiles) {
    addFailure(
      failures,
      filePath,
      'local primitive catalogs are not allowed in app workspaces; promote reusable UI to chat-ui/src/ui/primitives.'
    );
  }
}

function validateAppsPageUsesCollectionPrimitives(failures) {
  const appsPagePath = path.join(
    repoRoot,
    'factory_app',
    'app',
    'ui',
    'pages',
    'custom',
    'console',
    'AppsPage.jsx'
  );
  if (!exists(appsPagePath)) return;

  const source = fs.readFileSync(appsPagePath, 'utf8');
  const requiredTokens = [
    "from '@mozaiks/chat-ui/ui'",
    'CollectionToolbar',
    'ResourceList',
  ];

  for (const token of requiredTokens) {
    if (!source.includes(token)) {
      addFailure(
        failures,
        appsPagePath,
        `Apps page must compose shared collection primitives; missing "${token}".`
      );
    }
  }

  if (/<\s*table\b|<\s*thead\b|<\s*tbody\b|<\s*tr\b/.test(source)) {
    addFailure(
      failures,
      appsPagePath,
      'Apps page must use ResourceList for collection rendering instead of hand-coded table markup.'
    );
  }
}

/**
 * Names exported from the shared primitive barrel.
 *
 * Used to catch local re-implementations. Import rules alone do not catch this:
 * a file that defines its own `function Metric` imports nothing and passes every
 * other check, while rendering a drifted copy of a primitive that already exists.
 */
function sharedPrimitiveNames() {
  if (!exists(primitiveBarrelPath)) return new Set();

  const source = fs.readFileSync(primitiveBarrelPath, 'utf8');
  const names = new Set();

  // export { A, B as C } from './X.jsx';  /  export { A, B };
  for (const block of source.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const clause of block[1].split(',')) {
      const name = clause.includes(' as ')
        ? clause.split(' as ')[1]
        : clause;
      const trimmed = name.trim();
      if (/^[A-Z][A-Za-z0-9_]*$/.test(trimmed)) names.add(trimmed);
    }
  }
  return names;
}

// Internal top-level PascalCase declarations — the shape a hand-rolled component
// takes. One literal rather than a regex built per name, so nothing depends on
// escaping a name.
//
// `export`ed declarations are deliberately excluded. A file that exports its own
// `ActionButton` is publishing a local API — a name collision worth discussing,
// but a different problem from a private helper quietly re-drawing a primitive
// that already exists. Every clone found so far has been the private kind.
const DECLARATION_PATTERN =
  /(?:^|\n)\s*(?:function|class|const|let|var)\s+([A-Z][A-Za-z0-9_]*)\s*[=({]/g;

function validateNoLocalPrimitiveClones(failures) {
  const shared = sharedPrimitiveNames();
  if (shared.size === 0) return;

  const reactFiles = reactSurfaceRoots.flatMap((root) => (
    walk(root, new Set(['.js', '.jsx', '.ts', '.tsx']))
  ));

  for (const filePath of reactFiles) {
    const source = fs.readFileSync(filePath, 'utf8');
    const seen = new Set();

    DECLARATION_PATTERN.lastIndex = 0;
    let match;
    while ((match = DECLARATION_PATTERN.exec(source)) !== null) {
      const name = match[1];
      if (!shared.has(name) || seen.has(name)) continue;
      seen.add(name);
      addFailure(
        failures,
        filePath,
        `declares its own "${name}", which is a shared primitive. `
        + 'Import it from @mozaiks/chat-ui/ui instead of re-implementing it.'
      );
    }
  }
}

function main() {
  const failures = [];

  validatePagePrimitives(failures);
  validateReactImports(failures);
  validateNoLocalPrimitiveCatalogs(failures);
  validateNoLocalPrimitiveClones(failures);
  validateAppsPageUsesCollectionPrimitives(failures);

  if (failures.length > 0) {
    console.error('UI primitive usage validation failed:');
    for (const failure of failures) {
      console.error(`- ${failure}`);
    }
    process.exit(1);
  }

  console.log('UI primitive usage is aligned');
}

main();
