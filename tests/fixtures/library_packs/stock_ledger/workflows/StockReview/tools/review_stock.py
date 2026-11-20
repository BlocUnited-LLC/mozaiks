from _shared.tools.stock_lookup import stock_lookup


async def review_stock(context_variables=None) -> str:
    return await stock_lookup(context_variables=context_variables)
