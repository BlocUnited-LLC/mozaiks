from typing import TypedDict


class Task(TypedDict):
    id: str
    title: str
    project_id: str
    status: str
