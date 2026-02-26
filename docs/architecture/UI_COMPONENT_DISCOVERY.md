# UI Component Discovery Contract

> **Version:** 1.0.0  
> **Status:** Stable  
> **Module:** `@mozaiks/workflow-ui-discovery`

## Overview

The UI Component Discovery system automatically generates `index.js` export files for workflow UI components. This eliminates manual maintenance of component exports and ensures consistency across deployments.

## Contract

### Trigger Conditions

| Condition | Action |
|-----------|--------|
| Runtime startup (dev) | Run discovery, start watcher |
| Runtime startup (prod) | Run discovery once |
| File added/removed in `components/` | Regenerate component index |
| `theme_config.json` added/changed | Regenerate workflow index |
| Build step | Run discovery before bundling |

### Directory Structure Contract

The discovery system expects this structure:

```
{workflows_root}/
├── {WorkflowName}/
│   ├── theme_config.json     ← Optional: triggers workflow index
│   ├── index.js              ← AUTO-GENERATED (if theme_config.json exists)
│   └── components/
│       ├── index.js          ← AUTO-GENERATED
│       └── *.{js,jsx,ts,tsx} ← Component files
├── _shared/
│   └── components/
│       ├── index.js          ← AUTO-GENERATED
│       └── *.{js,jsx,ts,tsx} ← Shared components
└── index.js                  ← AUTO-GENERATED (root registry)
```

### Generated File Format

#### Component Index (`{workflow}/components/index.js`)

```javascript
/**
 * AUTO-GENERATED FILE - DO NOT EDIT
 * Generated: {ISO timestamp}
 */

export { default as ComponentName } from './ComponentName';
// ... one export per component file
```

#### Workflow Index (`{workflow}/index.js`)

Generated only when `theme_config.json` (or configured config file) exists:

```javascript
/**
 * AUTO-GENERATED FILE - DO NOT EDIT
 * Workflow: {WorkflowName}
 * Generated: {ISO timestamp}
 */

import themeConfig from './theme_config.json';

export const {WorkflowName}Workflow = {
  id: '{WorkflowName}',
  name: '{Display Name}',
  description: '{Display Name} Workflow',
  config: themeConfig,
};

export default {WorkflowName}Workflow;
```

#### Root Registry (`workflows/index.js`)

```javascript
/**
 * AUTO-GENERATED FILE - DO NOT EDIT
 */

// Shared components
export * as shared from './_shared/components/index.js';

// Per-workflow exports
export * as WorkflowA from './WorkflowA/components/index.js';

// Lazy-loading registry
export const workflowRegistry = {
  'WorkflowA': () => import('./WorkflowA/components/index.js'),
};

// Helper function
export async function getWorkflowComponents(workflowName) { ... }
```

### Configuration Schema

```yaml
ui_component_discovery:
  enabled: true                    # Enable/disable discovery
  scan_paths:                      # Paths to scan
    - "frontend/workflows/*/components/"
    - "frontend/workflows/_shared/components/"
  exclude_files:                   # Files to exclude
    - "index.js"
    - "*.test.js"
    - "*.spec.js"
    - "*.stories.js"
  component_extensions:            # Valid extensions
    - ".js"
    - ".jsx"
    - ".ts"
    - ".tsx"
  auto_generate_index: true        # Generate index files
  generate_root_registry: true     # Generate root registry
  generate_workflow_index: true    # Generate workflow-level index.js
  workflow_config_file: "theme_config.json"  # Triggers workflow index
  index_template: "esm"            # "esm" | "cjs" | "typescript"
  watch_mode: true                 # Watch in development
  watch_debounce_ms: 300           # Debounce delay
  on_startup: "generate"           # "generate" | "validate" | "skip"
  on_build: "generate"             # "generate" | "validate" | "skip"
```

### API Contract

#### Runtime Integration

```javascript
import { initializeComponentDiscovery, cleanupComponentDiscovery } 
  from '@mozaiks/workflow-ui-discovery/runtime';

// On startup
const state = await initializeComponentDiscovery({
  baseDir: process.cwd(),
  ui_component_discovery: { ... }
});

// On shutdown
cleanupComponentDiscovery(state);
```

#### Programmatic Usage

```javascript
import { discover, watch } from '@mozaiks/workflow-ui-discovery';

// One-time
const result = discover('./frontend/workflows');

// Watch mode
const watcher = await watch('./frontend/workflows', {
  onRegenerate: ({ workflow }) => { ... },
  onError: ({ workflow, error }) => { ... }
});
watcher.stop();
```

#### CLI Usage

```bash
# One-time generation
workflow-ui-discover ./frontend/workflows

# Watch mode
workflow-ui-discover --watch ./frontend/workflows

# With config
workflow-ui-discover -c ./config/ui-discovery.json ./frontend/workflows
```

### Guarantees

1. **Idempotency**: Running discovery multiple times with no file changes produces no writes
2. **Atomic Updates**: Index files are written only when content changes
3. **Deterministic Output**: Same input files always produce same output (ignoring timestamp)
4. **No Manual Steps**: Self-hosters never need to run scripts manually
5. **Hot Reload Compatible**: Changes are detected and applied without restart

### Error Handling

| Error | Behavior |
|-------|----------|
| Workflows directory not found | Log warning, skip discovery |
| Component file parse error | Log error, skip that component |
| Write permission denied | Throw error, fail discovery |
| Watcher setup fails | Log error, continue without watching |

## Usage Examples

### Self-Hosted Deployment

No manual steps required. The runtime automatically:
1. Detects workflows directory on startup
2. Generates/validates index files
3. Starts watcher in development mode

### Platform Integration

Platform can customize behavior via configuration:

```javascript
// In platform runtime config
{
  ui_component_discovery: {
    enabled: true,
    on_startup: 'validate',  // Don't modify files, just warn
    watch_mode: false        // No watching in production
  }
}
```

### Adding a New Component

1. Create `frontend/workflows/MyWorkflow/components/NewComponent.jsx`
2. The index is automatically regenerated (dev mode) or on next build (prod)
3. Import works immediately: `import { NewComponent } from '../components'`