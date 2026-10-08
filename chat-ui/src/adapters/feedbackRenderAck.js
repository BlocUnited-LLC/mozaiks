export async function acknowledgeFeedbackRender(eventId, { baseUrl = '', token, fetchImpl = fetch } = {}) {
  if (!eventId) return false;
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetchImpl(`${baseUrl || ''}/api/workflow-feedback/rendered`, {
    method: 'POST', headers, body: JSON.stringify({ event_id: eventId }),
  });
  if (!response.ok) return false;
  const body = await response.json().catch(() => null);
  return body?.status === 'success';
}
