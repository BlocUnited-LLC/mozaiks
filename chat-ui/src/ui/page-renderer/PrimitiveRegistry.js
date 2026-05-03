/**
 * PrimitiveRegistry — maps AppPageSection.primitive type strings to React components.
 *
 * Agents and page schemas reference primitives by name only (e.g. "DataTable").
 * This registry is the single bridge between the declarative schema layer and
 * the component layer. Nothing outside this file should import primitives directly
 * for the purpose of schema-driven rendering.
 *
 * To add a new primitive: import it and add an entry to PRIMITIVES.
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

const PRIMITIVES = {
  DataTable,
  Form,
  Card,
  Stat,
  Grid,
  Button,
  Modal,
  Alert,
  Badge,
  Skeleton,
  Empty,
  Timeline,
  CodeBlock,
  ProgressTracker,
  AlertBanner,
  ActionButton,
  FileList,
};

/**
 * Resolve a primitive type string to its React component.
 * Returns null if the type is not registered.
 *
 * @param {string} type  - e.g. "DataTable"
 * @returns {React.ComponentType|null}
 */
export function getPrimitive(type) {
  return PRIMITIVES[type] ?? null;
}

/**
 * All registered primitive type names.
 * @returns {string[]}
 */
export function getPrimitiveNames() {
  return Object.keys(PRIMITIVES);
}

export { PRIMITIVES };
