// An editable composer can remain visible while an agent is still working.
// Reply only when the chat status explicitly requests a composer response.
export async function isWorkflowReplyRequested(page) {
  const status = page.getByRole('group', { name: 'Chat status' });
  if (!(await status.isVisible().catch(() => false))) return false;
  const waiting = status.getByText('Waiting for you', { exact: true });
  const reply = status.getByText('Reply to continue', { exact: true });
  return await waiting.isVisible().catch(() => false)
    && await reply.isVisible().catch(() => false);
}
