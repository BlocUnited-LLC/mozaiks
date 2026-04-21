/**
 * @mozaiks/chat-ui — App UI primitives layer (Layers 1–3)
 *
 * Mozaiks primitives are the controlled vocabulary of UI building blocks
 * that agents and page schemas can compose. Each primitive:
 *   - Has a typed schema defined in DESIGN_SYSTEM_SPEC.md
 *   - Maps to base components from ../base/
 *   - Subscribes to typed ui.* events from the agent WebSocket bus
 *
 * Agents NEVER import raw base components. All composition goes through
 * primitives. Page schemas reference primitives by type name (e.g. "DataTable").
 *
 * Event bus:
 *   import { mountAppEventBridge } from '../hooks/useAppEventBus.js';
 *   mountAppEventBridge(ws); // call once when WS connects
 *
 * Layer 3 (page renderer):
 *   TODO — PrimitiveRegistry and PageRenderer will be added here.
 */

export { DataTable } from './DataTable.jsx';
export { Form }      from './Form.jsx';
export { Card }      from './Card.jsx';
export { Stat }      from './Stat.jsx';
export { Grid }      from './Grid.jsx';
export { Button }    from './Button.jsx';
export { Modal }     from './Modal.jsx';
export { Alert }     from './Alert.jsx';
export { Badge }     from './Badge.jsx';
export { Skeleton, Empty } from './Skeleton.jsx';

// Event bus — exported so App.jsx / providers can wire the WebSocket bridge
export { mountAppEventBridge, useAppEvent, emitAppEvent } from '../hooks/useAppEventBus.js';

export const _PRIMITIVES_LAYER_READY = true;
