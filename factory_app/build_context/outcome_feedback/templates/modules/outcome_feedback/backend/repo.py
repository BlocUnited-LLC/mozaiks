from mozaiksai.core.runtime.persistence import is_unique_constraint_violation


def _records(ctx):
    return ctx.persistence.collection("outcome_feedback", "records")


async def insert_once(ctx, *, record):
    try:
        result = await _records(ctx).update_one(
            {"feedback_id": record["feedback_id"], "user_id": ctx.user_id},
            {"$setOnInsert": record}, upsert=True,
        )
    except Exception as error:
        if not is_unique_constraint_violation(error):
            raise
        # A concurrent delivery of the same immutable receipt already won.
        existing = await _records(ctx).find_one(
            {"feedback_id": record["feedback_id"], "user_id": ctx.user_id},
        )
        identity_fields = ("app_id", "chat_id", "user_id", "outcome_id")
        if existing is None or any(existing.get(field) != record.get(field) for field in identity_fields):
            raise
        return False
    return bool(getattr(result, "upserted_id", None))


async def list_mine(ctx, *, limit):
    return await _records(ctx).find_many(
        {"user_id": ctx.user_id}, limit=limit, sort=[("observed_at", -1)],
        projection={"_id": 0},
    )
