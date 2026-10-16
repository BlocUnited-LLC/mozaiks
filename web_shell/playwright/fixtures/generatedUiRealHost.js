import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { matchPath } from 'react-router-dom';

export const generatedUiProjects = ['generated-desktop', 'generated-mobile'];
const routeKinds = new Set(['declarative', 'custom', 'auth', 'workflow', 'extension']);
const nonempty = (value) => typeof value === 'string' && value.trim().length > 0;
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export function generatedUiMode(env = process.env) {
  const mode = env.MOZAIKS_GENERATED_UI_MODE ?? 'mocked_rendering_smoke';
  if (!['mocked_rendering_smoke', 'real_host'].includes(mode)) {
    throw new Error('Unknown MOZAIKS_GENERATED_UI_MODE.');
  }
  return mode;
}

export function loopbackBaseUrl(value) {
  if (!nonempty(value)) throw new Error('Real-host acceptance requires MOZAIKS_GENERATED_UI_BASE_URL.');
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)
      || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Real-host base URL must be an explicit loopback HTTP(S) origin.');
  }
  return url.origin;
}

function localPath(value) {
  return nonempty(value) && value.startsWith('/') && !value.startsWith('//')
    && !value.includes('\\') && new URL(value, 'http://localhost').origin === 'http://localhost';
}

export function validateRealHostProfile(profile) {
  if (!object(profile) || !object(profile.candidate)) throw new Error('Missing real-host candidate identity.');
  const candidate = profile.candidate;
  if (Object.keys(candidate).sort().join(',') !== 'app_id,bundle_sha256,revision,source_sha'
      || !nonempty(candidate.app_id) || !nonempty(candidate.revision)
      || typeof candidate.source_sha !== 'string' || !/^[a-f0-9]{40}$/.test(candidate.source_sha)
      || typeof candidate.bundle_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(candidate.bundle_sha256)) {
    throw new Error('Invalid real-host candidate identity.');
  }
  if (!Array.isArray(profile.routes) || !profile.routes.length
      || !Array.isArray(profile.cases) || !profile.cases.length) {
    throw new Error('Real-host profile requires a nonempty route and case inventory.');
  }
  const routes = new Map();
  for (const route of profile.routes) {
    if (!object(route) || !localPath(route.path) || route.path.includes('?') || route.path.includes('#')
        || !nonempty(route.component) || !routeKinds.has(route.kind) || routes.has(route.path)
        || (route.schema != null && !nonempty(route.schema))
        || (route.heading != null && !nonempty(route.heading))) {
      throw new Error('Invalid or duplicate real-host route.');
    }
    if (route.excluded_reason != null) {
      if (!nonempty(route.excluded_reason) || ['declarative', 'custom'].includes(route.kind)) {
        throw new Error('App page routes cannot be excluded; other exclusions require a reason.');
      }
    } else {
      if (!nonempty(route.heading)) throw new Error(`Required route ${route.path} needs a reviewed heading.`);
      const visit = route.visit_path ?? route.path;
      if (!localPath(visit) || /[:*]/.test(new URL(visit, 'http://localhost').pathname)
          || !matchPath({ path: route.path, end: true }, new URL(visit, 'http://localhost').pathname)) {
        throw new Error(`Required route ${route.path} needs a matching concrete visit_path.`);
      }
    }
    routes.set(route.path, route);
  }
  const cases = new Set();
  for (const entry of profile.cases) {
    const route = object(entry) && routes.get(entry.route);
    const key = JSON.stringify([entry?.id, entry?.project]);
    if (!route || route.excluded_reason != null || !nonempty(entry.id)
        || !generatedUiProjects.includes(entry.project) || cases.has(key)) {
      throw new Error('Invalid, excluded, or duplicate real-host case.');
    }
    cases.add(key);
  }
  for (const route of routes.values()) {
    if (route.excluded_reason != null) continue;
    for (const project of generatedUiProjects) {
      if (!profile.cases.some((entry) => entry.route === route.path && entry.project === project)) {
        throw new Error(`Required route ${route.path} has no case for ${project}.`);
      }
    }
  }
  return profile;
}

export function realHostSettings(shellRoot, env = process.env) {
  const baseURL = loopbackBaseUrl(env.MOZAIKS_GENERATED_UI_BASE_URL);
  if (!nonempty(env.MOZAIKS_GENERATED_UI_PROFILE)) throw new Error('Real-host acceptance requires a reviewed profile.');
  const profileBytes = fs.readFileSync(env.MOZAIKS_GENERATED_UI_PROFILE);
  const expectedHash = env.MOZAIKS_GENERATED_UI_PROFILE_SHA256;
  if (typeof expectedHash !== 'string' || !/^[a-f0-9]{64}$/.test(expectedHash)
      || createHash('sha256').update(profileBytes).digest('hex') !== expectedHash) {
    throw new Error('Real-host profile bytes differ from the reviewed SHA-256.');
  }
  const profile = validateRealHostProfile(JSON.parse(profileBytes.toString('utf8')));
  const runId = env.MOZAIKS_GENERATED_UI_RUN_ID;
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(runId)) {
    throw new Error('Real-host acceptance requires a UUID run identity.');
  }
  const spec = env.MOZAIKS_GENERATED_UI_SPEC ?? 'playwright/generated-ui/generated-app.real-host.acceptance.spec.js';
  const specRoot = fs.realpathSync(path.join(shellRoot, 'playwright', 'generated-ui'));
  const specPath = path.resolve(shellRoot, spec);
  const relative = path.relative(specRoot, fs.realpathSync(specPath));
  if (path.isAbsolute(spec) || !spec.replaceAll('\\', '/').startsWith('playwright/generated-ui/')
      || !spec.endsWith('.spec.js') || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('Real-host spec must resolve within playwright/generated-ui.');
  }
  return { baseURL, profile, runId, specPath };
}

export function routeInventory(routes) {
  if (!Array.isArray(routes)) throw new Error('Shell has no route inventory.');
  const seen = new Set();
  return routes.map((route) => {
    if (!object(route) || !localPath(route.path) || !nonempty(route.component) || seen.has(route.path)
        || (route.schema != null && !nonempty(route.schema))) {
      throw new Error('Shell has an invalid or duplicate route.');
    }
    seen.add(route.path);
    return { path: route.path, component: route.component, schema: route.schema ?? null };
  }).sort((a, b) => a.path.localeCompare(b.path));
}
