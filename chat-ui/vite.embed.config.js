import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * Vite config for building the standalone embed.js bundle.
 *
 * Produces a single self-contained JS file that includes React and
 * the MozaiksEmbed component. Host apps load it via <script> tag.
 *
 * Build: npm run build:embed
 * Output: dist/embed.js
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@chat-workflows/index': fileURLToPath(new URL('./src/embed/workflowRegistryStub.js', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    lib: {
      entry: 'src/embed/embed.js',
      name: 'MozaiksEmbed',
      fileName: () => 'embed.js',
      formats: ['iife'],
    },
    rollupOptions: {
      // Bundle everything — host app does NOT need React
      external: [],
      output: {
        inlineDynamicImports: true,
      },
    },
    minify: 'terser',
    sourcemap: true,
  },
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
});
