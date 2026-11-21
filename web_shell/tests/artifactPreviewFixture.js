import path from 'node:path';
import { fileURLToPath } from 'node:url';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const component = path.resolve(shell, '../chat-ui/src/components/preview/ArtifactPreviewPane.jsx');

// Match Vite's runtime deduplication when the two packages have independent installations.
export const reactAlias = {
  react: path.join(shell, 'node_modules/react'),
  'react-dom': path.join(shell, 'node_modules/react-dom'),
};

// Use the actual public preview export without loading unrelated shell services into an isolated fixture.
export const previewExport = {
  name: 'preview-export',
  setup(builder) {
    builder.onResolve({ filter: /^@mozaiks\/chat-ui$/ }, () => ({ path: 'preview', namespace: 'preview-export' }));
    builder.onLoad({ filter: /.*/, namespace: 'preview-export' }, () => ({
      contents: `export { default as ArtifactPreviewPane } from ${JSON.stringify(component)}`,
      resolveDir: shell,
    }));
  },
};
