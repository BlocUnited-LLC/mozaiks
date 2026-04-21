/**
 * embed.js — Script-tag entry point for MozaiksEmbed.
 *
 * This file is the build entry for a standalone <script> tag distribution.
 * It reads data attributes from the script tag and auto-mounts the widget.
 *
 * Usage:
 *   <script src="https://mozaiks.ai/embed.js"
 *     data-app-id="my-app"
 *     data-runtime-url="https://api.mozaiks.ai"
 *     data-workflow-name="SupportWorkflow"
 *     data-theme-url="/api/themes/my-app"
 *     data-user-id="current-user-id"
 *     data-position="bottom-right"
 *     data-mode="floating">
 *   </script>
 *
 * Built with: npm run build:embed (produces dist/embed.js)
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import { MozaiksEmbed } from './MozaiksEmbed.jsx';

function init() {
  // Find the script tag that loaded us
  const script =
    document.currentScript ||
    document.querySelector('script[data-app-id][src*="embed"]');

  if (!script) {
    console.warn('[MozaiksEmbed] Could not find embed script tag. Use MozaiksEmbed.init() instead.');
    return;
  }

  const appId = script.dataset.appId;
  const runtimeUrl = script.dataset.runtimeUrl;
  const workflowName = script.dataset.workflowName || null;
  const themeUrl = script.dataset.themeUrl || null;
  const userId = script.dataset.userId || null;
  const authToken = script.dataset.authToken || null;
  const position = script.dataset.position || 'bottom-right';
  const mode = script.dataset.mode || 'floating';
  const defaultOpen = script.dataset.defaultOpen === 'true';

  if (!appId || !runtimeUrl) {
    console.error('[MozaiksEmbed] data-app-id and data-runtime-url are required.');
    return;
  }

  // Create mount point
  const mountId = 'mozaiks-embed-root';
  let container = document.getElementById(mountId);
  if (!container) {
    container = document.createElement('div');
    container.id = mountId;
    container.style.cssText = 'position:fixed;z-index:99999;pointer-events:none;inset:0;';
    document.body.appendChild(container);
  }

  // Allow pointer events on children only
  const style = document.createElement('style');
  style.textContent = `
    #${mountId} > * { pointer-events: auto; }
    .mozaiks-typing-dots {
      display: inline-flex;
      gap: 2px;
    }
    .mozaiks-typing-dots::after {
      content: '...';
      animation: mozaiks-dots 1.5s steps(4, end) infinite;
    }
    @keyframes mozaiks-dots {
      0%, 20% { content: '.'; }
      40% { content: '..'; }
      60%, 100% { content: '...'; }
    }
  `;
  document.head.appendChild(style);

  const root = createRoot(container);
  root.render(
    React.createElement(MozaiksEmbed, {
      appId,
      runtimeUrl,
      workflowName,
      themeUrl,
      userId,
      authToken,
      position,
      mode,
      defaultOpen,
    })
  );
}

// Auto-init when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

// Also expose a manual init API for programmatic usage
window.MozaiksEmbed = {
  init: (config) => {
    const mountId = config.mountId || 'mozaiks-embed-root';
    let container = document.getElementById(mountId);
    if (!container) {
      container = document.createElement('div');
      container.id = mountId;
      container.style.cssText = 'position:fixed;z-index:99999;pointer-events:none;inset:0;';
      document.body.appendChild(container);
    }

    const root = createRoot(container);
    root.render(
      React.createElement(MozaiksEmbed, {
        appId: config.appId,
        runtimeUrl: config.runtimeUrl,
        workflowName: config.workflowName || null,
        theme: config.theme || null,
        themeUrl: config.themeUrl || null,
        userId: config.userId || null,
        authToken: config.authToken || null,
        initialContext: config.initialContext || null,
        triggerMeta: config.triggerMeta || null,
        position: config.position || 'bottom-right',
        mode: config.mode || 'floating',
        defaultOpen: config.defaultOpen || false,
        onReady: config.onReady || null,
        onMessage: config.onMessage || null,
        onError: config.onError || null,
      })
    );

    return { container, root };
  },
};
