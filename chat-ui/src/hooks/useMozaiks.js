import { useCallback, useContext } from 'react';
import { appApi } from '../adapters/api';
import config from '../config';
import { NavigationContext } from '../providers/NavigationProvider';

/**
 * Hook for triggering workflows from any UI element.
 *
 * Usage:
 * ```jsx
 * import { useMozaiks } from '@mozaiks/chat-ui';
 *
 * function OrderPage({ order }) {
 *   const { startWorkflow } = useMozaiks();
 *
 *   return (
 *     <button onClick={() => startWorkflow('CustomerSupport', {
 *       context: { order_id: order.id }
 *     })}>
 *       Get Help
 *     </button>
 *   );
 * }
 * ```
 *
 * @returns {Object} Mozaiks workflow controls
 * @returns {Function} startWorkflow - Start a workflow with optional context
 */
export function useMozaiks() {
  const navContext = useContext(NavigationContext);

  /**
   * Start a workflow programmatically.
   *
   * @param {string} workflowName - Name of the workflow to start
   * @param {Object} options - Optional configuration
   * @param {Object} options.context - Context variables to pass to the workflow
   * @param {string} options.userId - User ID (defaults to current user)
   * @param {string} options.appId - App ID (defaults to configured app)
   * @returns {Promise<Object>} Result with chat_id and workflow info
   */
  const startWorkflow = useCallback(async (workflowName, options = {}) => {
    const { context, userId, appId } = options;

    // Get app ID from options, config, or navigation context
    const resolvedAppId = appId
      || config.get('chat.defaultAppId')
      || 'default';

    // User ID should come from auth - for now allow override
    const resolvedUserId = userId || 'anonymous';

    try {
      // Start the chat session via API
      const result = await appApi.startChat(
        resolvedAppId,
        workflowName,
        resolvedUserId,
        {},
        context || null
      );

      if (result.chat_id) {
        // Dispatch custom event for chat widget/interface to pick up
        window.dispatchEvent(new CustomEvent('mozaiks:workflow:start', {
          detail: {
            workflowName,
            chatId: result.chat_id,
            context,
            appId: resolvedAppId,
            userId: resolvedUserId,
          }
        }));

        return {
          success: true,
          chatId: result.chat_id,
          workflowName,
        };
      }

      return {
        success: false,
        error: result.error || 'Failed to start workflow',
      };
    } catch (error) {
      console.error('[useMozaiks] Failed to start workflow:', error);
      return {
        success: false,
        error: error.message,
      };
    }
  }, []);

  return {
    startWorkflow,
    // Future: add more controls like sendMessage, getMessages, etc.
  };
}

export default useMozaiks;
