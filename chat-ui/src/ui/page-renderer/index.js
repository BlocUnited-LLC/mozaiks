/**
 * @mozaiks/chat-ui — Page renderer layer (Layer 3)
 *
 * Connects declarative AppPageSchema (from pages/*.yaml) to the live
 * primitive component tree. This is the rendering contract between
 * agent-generated schemas and the platform shell.
 *
 * Usage:
 *   import { PageRenderer } from '@chat-ui/ui/page-renderer';
 *   <PageRenderer schema={parsedPageSchema} />
 *
 * The schema is loaded by SchemaPage (screens/SchemaPage.jsx) which
 * fetches it from /api/pages/{name} and passes it here.
 */

export { PageRenderer }       from './PageRenderer.jsx';
export { PageFrame }          from './PageFrame.jsx';
export { SectionRenderer }    from './SectionRenderer.jsx';
export { getPrimitive, getPrimitiveNames, PRIMITIVES } from './PrimitiveRegistry.js';
export { usePageData }        from './usePageData.js';
