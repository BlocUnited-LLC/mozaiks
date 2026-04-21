/**
 * @mozaiks/chat-ui/embed — entry point for the embeddable widget.
 *
 * Two usage patterns:
 *
 * 1. React import:
 *    import { MozaiksEmbed } from '@mozaiks/chat-ui/embed'
 *    <MozaiksEmbed
 *      appId="x"
 *      runtimeUrl="https://..."
 *      workflowName="SupportWorkflow"
 *      theme={themeConfig}
 *    />
 *
 * 2. Script tag (auto-mounts via embed.js build):
 *    <script src="https://mozaiks.ai/embed.js"
 *      data-app-id="x"
 *      data-runtime-url="https://..."
 *      data-workflow-name="SupportWorkflow"
 *      data-theme-url="/api/themes/x">
 *    </script>
 */

export { MozaiksEmbed, applyThemeToContainer } from './MozaiksEmbed.jsx';
