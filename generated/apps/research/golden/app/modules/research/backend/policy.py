class ResearchPolicy:
    @staticmethod
    def scope_query(query, *, user_id=None):
        scoped = dict(query or {})
        if user_id:
            scoped["user_id"] = user_id
        return scoped
