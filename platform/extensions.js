/**
 * platform/extensions.js — Platform extension registration stub.
 *
 * This is the canonical extension contract for OSS app bundles.
 * The shell imports this file via the @platform/extensions alias (vite.config.js).
 *
 * For product platforms (e.g. mozaiks-platform), this file is NOT used —
 * vite.config.js resolves @platform/extensions to the product's ui/index.js
 * when PLATFORM_PATH points to a directory that has a ../ui/index.js.
 *
 * For standard OSS app bundles (no PLATFORM_PATH set, or PLATFORM_PATH pointing
 * to a directory without a ../ui/index.js), this stub is the entry point.
 *
 * To register custom pages or components, replace the no-op below with your
 * own registrations — or generate this file via AppGenerator.
 *
 * Contract: export a `register(registerComponent)` function.
 *   registerComponent(name, component, metadata?) — from componentRegistry
 *
 * Example:
 *   import React from 'react';
 *   import { MyDashboard } from './pages/MyDashboard.jsx';
 *
 *   export function register(registerComponent) {
 *     registerComponent('MyDashboard', MyDashboard, { description: '...' });
 *   }
 */

// No-op — a standard OSS app bundle with no custom pages registered.
// Pages are added by replacing this file or by AppGenerator output.
export function register(_registerComponent) {}
