/**
 * PrimitiveRegistry — maps AppPageSection.primitive type strings to React components
 * and their JSON Schema config contracts.
 *
 * Agents and page schemas reference primitives by name only (e.g. "DataTable").
 * This registry is the single bridge between the declarative schema layer and
 * the component layer. Nothing outside this file should import primitives directly
 * for the purpose of schema-driven rendering.
 *
 * Spacing/layout ownership rule:
 *   - Components own INTERNAL padding only (their own inner chrome).
 *   - PageFrame owns outer page inset and section gap.
 *   - This registry owns NOTHING about layout — it is component + schema only.
 *
 * To add a new primitive:
 *   1. Import the component below.
 *   2. Add its JSON Schema to PrimitiveSchemas.js.
 *   3. Add an entry to PRIMITIVES with { Component, schema }.
 *   4. Regenerate primitive_schemas.json (run: node scripts/export-primitive-schemas.js).
 */

import { DataTable }       from '../primitives/DataTable.jsx';
import { Form }            from '../primitives/Form.jsx';
import { Card }            from '../primitives/Card.jsx';
import { Stat }            from '../primitives/Stat.jsx';
import { Grid }            from '../primitives/Grid.jsx';
import { Button }          from '../primitives/Button.jsx';
import { Modal }           from '../primitives/Modal.jsx';
import { Alert }           from '../primitives/Alert.jsx';
import { Badge }           from '../primitives/Badge.jsx';
import { Skeleton, Empty } from '../primitives/Skeleton.jsx';
import { Timeline }        from '../primitives/Timeline.jsx';
import { CodeBlock }       from '../primitives/CodeBlock.jsx';
import { ProgressTracker } from '../primitives/ProgressTracker.jsx';
import { AlertBanner }     from '../primitives/AlertBanner.jsx';
import { ActionButton }    from '../primitives/ActionButton.jsx';
import { FileList }        from '../primitives/FileList.jsx';

import { PRIMITIVE_SCHEMAS } from './PrimitiveSchemas.js';

const PRIMITIVES = {
  DataTable:       { Component: DataTable,       schema: PRIMITIVE_SCHEMAS.DataTable },
  Form:            { Component: Form,            schema: PRIMITIVE_SCHEMAS.Form },
  Card:            { Component: Card,            schema: PRIMITIVE_SCHEMAS.Card },
  Stat:            { Component: Stat,            schema: PRIMITIVE_SCHEMAS.Stat },
  Grid:            { Component: Grid,            schema: PRIMITIVE_SCHEMAS.Grid },
  Button:          { Component: Button,          schema: PRIMITIVE_SCHEMAS.Button },
  Modal:           { Component: Modal,           schema: PRIMITIVE_SCHEMAS.Modal },
  Alert:           { Component: Alert,           schema: PRIMITIVE_SCHEMAS.Alert },
  Badge:           { Component: Badge,           schema: PRIMITIVE_SCHEMAS.Badge },
  Skeleton:        { Component: Skeleton,        schema: PRIMITIVE_SCHEMAS.Skeleton },
  Empty:           { Component: Empty,           schema: PRIMITIVE_SCHEMAS.Empty },
  Timeline:        { Component: Timeline,        schema: PRIMITIVE_SCHEMAS.Timeline },
  CodeBlock:       { Component: CodeBlock,       schema: PRIMITIVE_SCHEMAS.CodeBlock },
  ProgressTracker: { Component: ProgressTracker, schema: PRIMITIVE_SCHEMAS.ProgressTracker },
  AlertBanner:     { Component: AlertBanner,     schema: PRIMITIVE_SCHEMAS.AlertBanner },
  ActionButton:    { Component: ActionButton,    schema: PRIMITIVE_SCHEMAS.ActionButton },
  FileList:        { Component: FileList,        schema: PRIMITIVE_SCHEMAS.FileList },
};

/**
 * Resolve a primitive type string to its React component.
 * Returns null if the type is not registered.
 *
 * @param {string} type  - e.g. "DataTable"
 * @returns {React.ComponentType|null}
 */
export function getPrimitive(type) {
  return PRIMITIVES[type]?.Component ?? null;
}

/**
 * Resolve a primitive type string to its JSON Schema config contract.
 * Returns null if the type is not registered.
 *
 * @param {string} type  - e.g. "DataTable"
 * @returns {object|null}
 */
export function getPrimitiveSchema(type) {
  return PRIMITIVES[type]?.schema ?? null;
}

/**
 * All registered primitive type names.
 * @returns {string[]}
 */
export function getPrimitiveNames() {
  return Object.keys(PRIMITIVES);
}

/**
 * All registered primitive schemas keyed by primitive name.
 * Used by agent guidance injection (ui_primitives.py reads primitive_schemas.json
 * which is generated from this registry).
 * @returns {Record<string, object>}
 */
export function getAllPrimitiveSchemas() {
  return Object.fromEntries(
    Object.entries(PRIMITIVES).map(([name, entry]) => [name, entry.schema])
  );
}

export { PRIMITIVES };
