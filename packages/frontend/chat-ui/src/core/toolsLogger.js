// Lightweight tools logger for frontend UI tools/components
// Provides consistent console logging with context. Avoids leaking secrets.

export function createToolsLogger({ tool, eventId, workflowName, agentMessageId } = {}) {
  const base = { tool: tool || 'unknown', eventId, workflowName, agentMessageId };
  const stamp = () => new Date().toISOString();

  const withCtx = (level, msg, extra) => {
    const payload = { t: stamp(), ...base, ...(extra || {}) };
    // Standardize output; use console[level] if exists
    const fn = console[level] || console.log;
    fn(`[tool:${base.tool}] ${msg}`, payload);
  };

  return {
    info: (msg, extra) => withCtx('info', msg, extra),
    warn: (msg, extra) => withCtx('warn', msg, extra),
    error: (msg, extra) => withCtx('error', msg, extra),
    debug: (msg, extra) => withCtx('debug', msg, extra),
    event: (action, status = 'info', fields) => withCtx('log', `event:${action} (${status})`, fields),
  };
}
