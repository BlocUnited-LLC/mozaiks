def task_query(params):
    query = {}
    if params.get("project_id"):
        query["project_id"] = params["project_id"]
    if params.get("status"):
        query["status"] = params["status"]
    return query
