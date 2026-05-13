#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const repoRoot = path.resolve(__dirname, '..');
const schemaModulePath = path.join(
  repoRoot,
  'chat-ui',
  'src',
  'ui',
  'page-renderer',
  'PrimitiveSchemas.js'
);
const catalogModulePath = path.join(
  repoRoot,
  'chat-ui',
  'src',
  'ui',
  'page-renderer',
  'PrimitiveCatalog.js'
);
const registryPath = path.join(
  repoRoot,
  'chat-ui',
  'src',
  'ui',
  'page-renderer',
  'PrimitiveRegistry.js'
);
const outputPath = path.join(
  repoRoot,
  'chat-ui',
  'src',
  'ui',
  'page-renderer',
  'primitive_schemas.json'
);

const COMMENT =
  'Auto-derived from PrimitiveRegistry.js, PrimitiveSchemas.js, and PrimitiveCatalog.js. Do not edit manually. Consumed by mozaiksai/core/workflow/ui_primitives.py for agent guidance injection.';
const REGISTRY_ENTRY_RE =
  /^\s*([A-Za-z][A-Za-z0-9_]*):\s*\{\s*Component:\s*[A-Za-z][A-Za-z0-9_]*,\s*schema:\s*PRIMITIVE_SCHEMAS\.([A-Za-z][A-Za-z0-9_]*)\s*\}/gm;

function enumText(schema) {
  if (!Array.isArray(schema?.enum) || schema.enum.length === 0) {
    return '';
  }
  return `: ${schema.enum.join('|')}`;
}

function defaultText(schema) {
  if (!Object.prototype.hasOwnProperty.call(schema ?? {}, 'default')) {
    return '';
  }
  return ` — default ${String(schema.default)}`;
}

function objectShape(schema) {
  const properties = schema?.properties ?? {};
  const required = new Set(schema?.required ?? []);
  const keys = Object.keys(properties);

  if (keys.length === 0) {
    return 'object';
  }

  return `{${keys.map((key) => `${key}${required.has(key) ? '' : '?'}`).join(',')}}`;
}

function isActionSchema(schema) {
  const properties = schema?.properties ?? {};
  return Boolean(properties.label && properties.action_type);
}

function arrayItemText(items) {
  if (!items) {
    return 'items';
  }
  if (Array.isArray(items.oneOf)) {
    return items.oneOf.map((item) => describeSchema(item, { compact: true })).join('|');
  }
  if (items.type === 'object') {
    return objectShape(items);
  }
  return describeSchema(items, { compact: true });
}

function describeSchema(schema, { compact = false } = {}) {
  if (!schema || typeof schema !== 'object') {
    return 'any';
  }

  if (Array.isArray(schema.oneOf)) {
    return schema.oneOf.map((entry) => describeSchema(entry, { compact: true })).join('|');
  }

  if (schema.type === 'array') {
    const minText = schema.minItems ? ` — min ${schema.minItems}` : '';
    return `array of ${arrayItemText(schema.items)}${minText}`;
  }

  if (schema.type === 'object') {
    if (isActionSchema(schema)) {
      return 'action object';
    }
    return objectShape(schema);
  }

  if (schema.type) {
    return `${schema.type}${enumText(schema)}${defaultText(schema)}`;
  }

  return 'any';
}

function summarizePrimitive(schema, catalogEntry) {
  const required = schema.required ?? [];
  const properties = schema.properties ?? {};
  const requiredSet = new Set(required);

  return {
    tier: catalogEntry.tier,
    use: catalogEntry.use,
    avoid: catalogEntry.avoid,
    required,
    properties: Object.fromEntries(
      Object.entries(properties).map(([key, value]) => {
        const requiredText = requiredSet.has(key) ? ' — REQUIRED' : '';
        const descriptionText = value?.description ? ` — ${value.description}` : '';
        return [key, `${describeSchema(value)}${descriptionText}${requiredText}`];
      })
    ),
  };
}

function readPrimitiveRegistryEntries() {
  const source = fs.readFileSync(registryPath, 'utf8');
  const entries = [];
  let match;

  while ((match = REGISTRY_ENTRY_RE.exec(source)) !== null) {
    entries.push({
      name: match[1],
      schemaName: match[2],
    });
  }

  if (entries.length === 0) {
    throw new Error('No primitive registry entries found in PrimitiveRegistry.js');
  }

  return entries;
}

function assertNoDuplicates(values, label) {
  const seen = new Set();
  const duplicates = new Set();

  for (const value of values) {
    if (seen.has(value)) {
      duplicates.add(value);
    }
    seen.add(value);
  }

  if (duplicates.size > 0) {
    throw new Error(`${label} contains duplicate entries: ${Array.from(duplicates).join(', ')}`);
  }
}

function validateRegistrySchemas(registryEntries, primitiveSchemas) {
  const registryNames = registryEntries.map((entry) => entry.name);
  const schemaRefs = registryEntries.map((entry) => entry.schemaName);
  const schemaNames = Object.keys(primitiveSchemas);

  assertNoDuplicates(registryNames, 'PrimitiveRegistry.js');
  assertNoDuplicates(schemaRefs, 'PrimitiveRegistry.js schema references');
  assertNoDuplicates(schemaNames, 'PrimitiveSchemas.js');

  const mismatchedRefs = registryEntries
    .filter((entry) => entry.name !== entry.schemaName)
    .map((entry) => `${entry.name}->${entry.schemaName}`);
  if (mismatchedRefs.length > 0) {
    throw new Error(
      `Primitive registry names must match schema names: ${mismatchedRefs.join(', ')}`
    );
  }

  const schemaSet = new Set(schemaNames);
  const registrySet = new Set(registryNames);
  const missingSchemas = registryNames.filter((name) => !schemaSet.has(name));
  const unregisteredSchemas = schemaNames.filter((name) => !registrySet.has(name));

  if (missingSchemas.length > 0 || unregisteredSchemas.length > 0) {
    const messages = [];
    if (missingSchemas.length > 0) {
      messages.push(`missing schemas for registered primitives: ${missingSchemas.join(', ')}`);
    }
    if (unregisteredSchemas.length > 0) {
      messages.push(`schemas without registry entries: ${unregisteredSchemas.join(', ')}`);
    }
    throw new Error(messages.join('; '));
  }
}

function validatePrimitiveCatalog(registryEntries, primitiveCatalog) {
  const registryNames = registryEntries.map((entry) => entry.name);
  const catalogNames = Object.keys(primitiveCatalog);
  const registrySet = new Set(registryNames);
  const allowedTiers = new Set(['default', 'support', 'specialized']);

  assertNoDuplicates(catalogNames, 'PrimitiveCatalog.js');

  const missingCatalog = registryNames.filter((name) => !primitiveCatalog[name]);
  const extraCatalog = catalogNames.filter((name) => !registrySet.has(name));
  const invalidCatalog = catalogNames.filter((name) => {
    const entry = primitiveCatalog[name];
    return !allowedTiers.has(entry?.tier) || !entry?.use || !entry?.avoid;
  });

  if (missingCatalog.length > 0 || extraCatalog.length > 0 || invalidCatalog.length > 0) {
    const messages = [];
    if (missingCatalog.length > 0) {
      messages.push(`missing catalog entries: ${missingCatalog.join(', ')}`);
    }
    if (extraCatalog.length > 0) {
      messages.push(`catalog entries without registry entries: ${extraCatalog.join(', ')}`);
    }
    if (invalidCatalog.length > 0) {
      messages.push(`catalog entries must define tier/use/avoid: ${invalidCatalog.join(', ')}`);
    }
    throw new Error(messages.join('; '));
  }
}

async function main() {
  const checkOnly = process.argv.includes('--check');
  const { PRIMITIVE_SCHEMAS } = await import(pathToFileURL(schemaModulePath).href);
  const { PRIMITIVE_CATALOG } = await import(pathToFileURL(catalogModulePath).href);
  const registryEntries = readPrimitiveRegistryEntries();

  validateRegistrySchemas(registryEntries, PRIMITIVE_SCHEMAS);
  validatePrimitiveCatalog(registryEntries, PRIMITIVE_CATALOG);

  const output = {
    _comment: COMMENT,
    ...Object.fromEntries(
      registryEntries.map(({ name }) => [
        name,
        summarizePrimitive(PRIMITIVE_SCHEMAS[name], PRIMITIVE_CATALOG[name]),
      ])
    ),
  };
  const serialized = `${JSON.stringify(output, null, 2)}\n`;

  if (checkOnly) {
    const current = fs.existsSync(outputPath)
      ? fs.readFileSync(outputPath, 'utf8')
      : '';
    if (current !== serialized) {
      console.error('primitive_schemas.json is out of date. Run: node scripts/export-primitive-schemas.js');
      process.exit(1);
    }
    console.log('primitive_schemas.json is current');
    return;
  }

  fs.writeFileSync(outputPath, serialized);
  console.log(`Wrote ${path.relative(repoRoot, outputPath)}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
