import { ApprovalCard, DiagramViewer } from '@mozaiks/chat-ui';

export default function WorkflowPlanReview({ payload = {}, onResponse, workflowName }) {
  const respond = (response) => onResponse({ ...response, review_id: payload.review_id });
  return (
    <div className="min-w-0 space-y-4" key={payload.review_id}>
      {payload.workflow_count > 0 ? <DiagramViewer payload={payload} /> : null}
      <ApprovalCard
        payload={payload}
        workflowName={workflowName}
        onResponse={respond}
        onCancel={() => respond({ action: 'cancel', approved: false })}
      />
    </div>
  );
}
