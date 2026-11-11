// Shared assertions for the real notes traversal and its offline browser fixture.
import fs from 'node:fs';

export async function finishTraversal(receiptPath, generation, metadata, exerciseNotes = null) {
  const journey = generation.ok && exerciseNotes ? {} : null;
  let callbackReturned = false;
  const write = () => {
    const complete = callbackReturned && journey && journey.accepted_artifact && journey.preview_opened
      && journey.baseline_note_created && journey.refinement_submitted && journey.refinement_draft_id
      && journey.refined_create_list_and_search;
    const verdict = journey && !complete
      ? { ...generation, ok: false, outcome: 'NOTES_JOURNEY_FAILED', summary: 'NOTES JOURNEY FAILED - preview or refinement is incomplete' }
      : journey ? { ...generation, outcome: 'VALIDATED_NOTES_JOURNEY', summary: 'VALIDATED NOTES JOURNEY' } : generation;
    fs.writeFileSync(receiptPath, JSON.stringify({ ...verdict, generation_verdict: generation, journey, ...metadata }, null, 2), 'utf8');
    return verdict;
  };
  // An interruption after generation must leave a failing overall receipt.
  let verdict = write();
  if (journey) {
    try {
      await exerciseNotes(journey);
      callbackReturned = true;
    } catch (error) {
      journey.error = String(error.message || error).slice(0, 300);
    }
    verdict = write();
  }
  return verdict;
}

export function validatedDraftId(detail, parentId) {
  if (typeof parentId !== 'string' || !parentId) throw new Error('Original artifact identity is unavailable');
  const id = detail.match(/\bStatus:\s*validated\s*•\s*Draft\s+([A-Za-z0-9_-]+)/i)?.[1];
  if (!id || id === parentId) throw new Error('Refinement did not save a distinct validated draft');
  return id;
}

export function requirePassedDraft(artifacts, draftId, appId) {
  if (typeof draftId !== 'string' || !draftId || typeof appId !== 'string' || !appId) {
    throw new Error('Refined draft or app identity is unavailable');
  }
  const saved = artifacts?.find((artifact) => artifact.artifact_version_id === draftId);
  if (!saved || saved.app_id !== appId || saved.build_family !== 'app_bundle'
      || saved.app_validation_status !== 'passed') {
    throw new Error('Refined draft has no matching passed app validation evidence');
  }
  return saved;
}

export async function createNote(frame, title, body, timeout = 30000) {
  const open = frame.getByRole('button', { name: /^(new|add|create)( a)? note$/i }).first();
  if (await open.isVisible().catch(() => false)) await open.click();
  const titleField = frame.getByLabel(/title/i).or(frame.getByPlaceholder(/title/i)).first();
  const bodyField = frame.getByLabel(/body|content|note text/i).or(frame.getByPlaceholder(/body|content|note text/i)).first();
  await titleField.waitFor({ state: 'visible', timeout });
  await bodyField.waitFor({ state: 'visible', timeout });
  await titleField.fill(title);
  await bodyField.fill(body);
  await frame.getByRole('button', { name: /^(save|add|create)( a)?( note)?$/i }).first().click();
  await frame.getByText(title, { exact: false }).first().waitFor({ state: 'visible', timeout });
}

export async function verifyTitleSearch(frame, matchingTitle, otherTitle, query, timeout = 30000) {
  const match = frame.getByText(matchingTitle, { exact: false }).first();
  const other = frame.getByText(otherTitle, { exact: false }).first();
  // Both records must be present before filtering and after clearing the query.
  await match.waitFor({ state: 'visible', timeout });
  await other.waitFor({ state: 'visible', timeout });
  const search = frame.getByRole('searchbox').or(frame.getByLabel(/search/i))
    .or(frame.getByPlaceholder(/search/i)).first();
  await search.waitFor({ state: 'visible', timeout });
  await search.fill(query);
  await match.waitFor({ state: 'visible', timeout });
  await other.waitFor({ state: 'hidden', timeout });
  await search.fill('');
  await match.waitFor({ state: 'visible', timeout });
  await other.waitFor({ state: 'visible', timeout });
}
