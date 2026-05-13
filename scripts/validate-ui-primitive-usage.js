#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');
const primitiveSchemasPath = path.join(
  repoRoot,
  'chat-ui',
  'src',
  'ui',
  'page-renderer',
  'primitive_schemas.json'
);

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
      pattern: /ConsolePrimitives/g,
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

function main() {
  const failures = [];

  validatePagePrimitives(failures);
  validateReactImports(failures);
  validateNoLocalPrimitiveCatalogs(failures);
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
