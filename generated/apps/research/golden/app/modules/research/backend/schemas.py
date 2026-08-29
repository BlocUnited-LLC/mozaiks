from uuid import uuid4


def research_request(*, query):
    return {
        "research_id": uuid4().hex,
        "query": str(query).strip(),
        "status": "requested",
    }
