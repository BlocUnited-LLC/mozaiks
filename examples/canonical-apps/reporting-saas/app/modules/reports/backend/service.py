from .policy import report_query
from .repo import ReportsRepo


class ReportsService:
    def __init__(self, ctx):
        self.repo = ReportsRepo(ctx)

    async def view_reports(self, params):
        items = await self.repo.list(report_query(params))
        return {"items": items, "count": len(items)}

    async def export_report(self, params):
        report_id = str(params.get("report_id", "all"))
        return {"exported": True, "download_url": f"/exports/{report_id}.csv"}
