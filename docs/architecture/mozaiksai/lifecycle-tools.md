# Lifecycle Tools

Lifecycle tools are workflow hooks that run at orchestration boundaries.

Use them for:

- loading persisted context before a workflow starts
- persisting canonical fields after a workflow finishes
- setup and cleanup work
- observability and side-channel state preparation

Do not use them for:

- hidden business routing
- replacing handoffs
- replacing workflow graphs

## Typical Hook Points

- `before_chat`
- `after_chat`
- `before_agent`
- `after_agent`

## Related Docs

- [Workflow Authoring Contracts](../../architecture/workflows/workflow-authoring-contracts.md)
- [Workflow Task Batches](task-batches.md)
