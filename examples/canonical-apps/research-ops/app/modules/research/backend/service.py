from .repo import ResearchRepo


class ResearchService:
    def __init__(self, ctx):
        self.repo = ResearchRepo(ctx)

    async def summarize(self, params):
        source_text = params["source_text"].strip()
        summary = {"source_text": source_text, "summary": source_text[:160]}
        await self.repo.save(summary)
        return {"summary": summary["summary"]}

    async def start(self, params):
        return {
            "workflow_id": "ResearchWorkflow",
            "context_variables": {"source_text": params.get("source_text", "Local deterministic source")},
        }
