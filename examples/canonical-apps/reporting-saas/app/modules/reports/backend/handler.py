from .service import ReportsService


class ReportsHandler:
    async def view_report(self, ctx, **params):
        return await ReportsService(ctx).view_reports(params)

    async def export_report(self, ctx, **params):
        return await ReportsService(ctx).export_report(params)
