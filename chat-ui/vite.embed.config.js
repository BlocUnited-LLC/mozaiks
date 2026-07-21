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
      '@chat-workflows-root': fileURLToPath(new URL('./src/workflows_stub', import.meta.url)),
      // Vite 8 stubs optional peer deps; resolve to local devDep copies so they
      // are bundled into the self-contained embed output (host does not need React).
      'react': fileURLToPath(new URL('./node_modules/react', import.meta.url)),
      'react-dom': fileURLToPath(new URL('./node_modules/react-dom', import.meta.url)),
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
  // Vite 8 treats optional peer deps as stubs by default; override for embed
  // builds where react/react-dom must be bundled into the output.
  optimizeDeps: {
    include: ['react', 'react-dom', 'react-dom/client'],
  },
});
