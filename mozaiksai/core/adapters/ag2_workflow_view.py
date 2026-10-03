"""Keep AG2 workflow transcript replies visible to provider message mappers."""

from ag2.events import BaseEvent, ModelMessage, ModelResponse
from ag2.network import ChannelMetadata, Envelope, WorkflowAdapter
from ag2.network.views.base import EnvelopeRenderer, NameResolver, ViewPolicy, default_name_resolver


class _AssistantResponseView:
    def __init__(self, view: ViewPolicy) -> None:
        self._view = view
        self.name = view.name

    async def project(
        self,
        wal: list[Envelope],
        *,
        participant_id: str,
        channel: ChannelMetadata,
        render_envelope: EnvelopeRenderer,
        name_for: NameResolver = default_name_resolver,
    ) -> list[BaseEvent]:
        events = await self._view.project(
            wal, participant_id=participant_id, channel=channel,
            render_envelope=render_envelope, name_for=name_for,
        )
        # AG2-WP-015: native network views emit bare ModelMessage for self
        # history, while provider mappers consume ModelResponse. Normalize
        # only the already-projected event; AG2 owns visibility and windowing.
        return [ModelResponse(message=event) if isinstance(event, ModelMessage) else event for event in events]


class WorkflowHistoryAdapter(WorkflowAdapter):
    def default_view_policy(self, metadata: ChannelMetadata, participant_id: str) -> ViewPolicy:
        return _AssistantResponseView(super().default_view_policy(metadata, participant_id))
