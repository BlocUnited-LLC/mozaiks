def project_query(params):
    return {"status": params["status"]} if params.get("status") else {}
