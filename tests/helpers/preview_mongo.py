"""Atomic Mongo collection double; preview lifecycle remains production code."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError


def _value(document, path):
    value = document
    for name in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(name)
    return value


def _matches(document, query):
    for path, expected in query.items():
        actual = _value(document, path)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$gt" and not (actual is not None and actual > value):
                    return False
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$ne" and actual == value:
                    return False
                if operator not in {"$gt", "$in", "$ne"}:
                    raise AssertionError(f"Unsupported test Mongo operator: {operator}")
        elif actual != expected:
            return False
    return True


def _set(document, path, value):
    names = path.split(".")
    target = document
    for name in names[:-1]:
        target = target.setdefault(name, {})
    target[names[-1]] = deepcopy(value)


class FakePreviewCollection:
    def __init__(self):
        self.documents = {}

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name", "index")

    async def find_one(self, query):
        await asyncio.sleep(0)
        return next((deepcopy(doc) for doc in self.documents.values() if _matches(doc, query)), None)

    def find(self, query):
        collection = self

        class Cursor:
            async def to_list(self, length=None):
                return [deepcopy(doc) for doc in collection.documents.values() if _matches(doc, query)][:length]

        return Cursor()

    async def insert_one(self, document):
        await asyncio.sleep(0)
        if document["_id"] in self.documents:
            raise DuplicateKeyError("duplicate test id")
        self.documents[document["_id"]] = deepcopy(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def replace_one(self, query, replacement):
        await asyncio.sleep(0)
        for key, document in self.documents.items():
            if _matches(document, query):
                self.documents[key] = deepcopy(replacement)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def update_one(self, query, update):
        await asyncio.sleep(0)
        for document in self.documents.values():
            if _matches(document, query):
                for path, value in update.get("$set", {}).items():
                    _set(document, path, value)
                for path, value in update.get("$inc", {}).items():
                    _set(document, path, (_value(document, path) or 0) + value)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query):
        await asyncio.sleep(0)
        for key, document in self.documents.items():
            if _matches(document, query):
                del self.documents[key]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakePreviewDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakePreviewCollection())
