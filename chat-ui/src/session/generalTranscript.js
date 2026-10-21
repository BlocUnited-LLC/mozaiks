/** Shared presentation of persisted General Chat messages. */
export function mapGeneralMessage(message) {
  if (!message) return null;
  const timestamp = (() => {
    if (!message.timestamp) return Date.now();
    try {
      return new Date(message.timestamp).getTime();
    } catch (_) {
      return Date.now();
    }
  })();
  return {
    id: message.event_id || `general-${message.sequence || Date.now()}`,
    sender: message.role === 'assistant' ? 'agent' : 'user',
    agentName: message.role === 'assistant' ? 'Assistant' : 'You',
    content: message.content,
    isStreaming: false,
    timestamp,
    metadata: message.metadata || {},
  };
}
