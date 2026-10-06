from pymongo.errors import DuplicateKeyError


def _records(ctx):
    return ctx.persistence.collection("outcome_feedback", "records")


async def insert_once(ctx, *, record):
    try:
        result = await _records(ctx).update_one(
            {"feedback_id": record["feedback_id"], "user_id": ctx.user_id},
            {"$setOnInsert": record}, upsert=True,
        )
    except DuplicateKeyError:
        # A concurrent delivery of the same immutable receipt already won.
        existing = await _records(ctx).find_one(
            {"feedback_id": record["feedback_id"], "user_id": ctx.user_id},
        )
        if existing is None:
            raise
        return False
    return bool(getattr(result, "upserted_id", None))


async def list_mine(ctx, *, limit):
    return await _records(ctx).find_many(
        {"user_id": ctx.user_id}, limit=limit, sort=[("observed_at", -1)],
        projection={"_id": 0},
    )
