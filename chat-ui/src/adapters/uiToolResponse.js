export async function submitToolCallResponse(eventId, responseData, { baseUrl = '', token, fetchImpl = fetch } = {}) {
  if (!eventId) throw new Error('This review is no longer active. Reopen the workflow to load its current review.');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetchImpl(`${baseUrl || ''}/api/tool-call/respond`, {
      method: 'POST', headers,
      body: JSON.stringify({ event_id: eventId, response_data: responseData }),
    });
  } catch {
    throw new Error('The server did not confirm your decision. Reconnect to check the current review before retrying.');
  }
  if (response.status === 404) {
    throw new Error('This review is no longer active. Your decision was not applied. Reopen the workflow to load its current review.');
  }
  if (!response.ok) throw new Error('Your decision was not accepted. Please retry after reconnecting to the workflow.');
  const body = await response.json().catch(() => null);
  if (body?.status !== 'success') throw new Error('The server did not confirm your decision.');
  return true;
}
