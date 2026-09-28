from __future__ import annotations

from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    scan_module_persistence,
)

PATH = "modules/tasks/backend/repo.py"


@pytest.mark.parametrize("source", [
    "async def read(ctx):\n    return await ctx.persistence.collection('tasks', 'tasks')._collection.find_one({})",
    "async def read(ctx):\n    store = ctx.persistence\n    rows = store.collection('tasks', 'tasks')\n    alias = rows\n    return alias._collection",
    "async def read(ctx):\n    store = getattr(ctx, 'persistence')\n    getter = getattr\n    return getter(store, '_client_handle')()",
    "async def read(ctx):\n    store = ctx.persistence\n    return vars(store)",
    "from mozaiksai.core.runtime.persistence import MongoPersistenceContext as Store\nnew_store = Store\nstore = new_store(app_id='x')",
    "import mozaiksai.core.runtime.persistence as storage\nstore = storage.MongoPersistenceContext(app_id='x')",
    "from mozaiksai.core.runtime.persistence import PersistencePrincipal as Principal\nprincipal = Principal('victim')",
    "from mozaiksai.core.core_config import get_mongo_client as connect\nclient = connect()",
    "import mozaiksai.core.core_config as config\nconnect = config.get_mongo_client\nclient = connect()",
    "import motor.motor_asyncio as driver\nclient = driver.AsyncIOMotorClient()",
    "from pymongo import MongoClient as Client\nclient = Client()",
    "from importlib import import_module as load\ndriver = load('mozaiksai.core.runtime.' + 'persistence')",
    "driver = __import__('motor.motor_asyncio')",
    "def read(collection):\n    return collection._owner_scope",
    "def read(ctx):\n    rows = ctx.persistence.literal_collection('shared')\n    return rows._collection",
    "from mozaiksai.core.runtime.persistence import app_data_from_context as data\ndef read(ctx):\n    store = data(ctx)\n    rows = store.collection('shared.records')\n    return rows._raw",
    "def read(ctx):\n    expose = object.__getattribute__\n    return expose(ctx.persistence, '_client')",
    "import mozaiksai.core.runtime.persistence as storage\nconstructors = [storage.MongoPersistenceContext]\nstore = constructors[0](app_id='x')",
    "from mozaiksai.core.runtime.persistence.request_scope import bind_persistence_principal as bind\nbind(None)",
    "import mozaiksai.core.runtime.persistence.request_scope as scope\nvalue = scope.current_persistence_principal()",
    "from mozaiksai.core.runtime.persistence import request_scope as scope\nvalue = scope.current_persistence_principal()",
    "import mozaiksai.core.runtime.persistence as storage\nvalue = storage.PersistencePrincipal.from_authenticated_user(None)",
    "from mozaiksai.core.runtime.persistence import MongoPersistenceContext as Store\nvalue = Store.__new__(Store)",
    "def escape(store):\n    return store._client\ndef action(ctx):\n    ctx.persistence.collection('tasks', 'tasks')\n    return escape(ctx.persistence)",
    "from types import SimpleNamespace\ndef escape(handle):\n    handle._principal = SimpleNamespace(user_id='victim')\ndef action(ctx):\n    rows = ctx.persistence.collection('tasks', 'tasks')\n    escape(rows)\n    return rows.find_many({})",
    "def read(ctx):\n    direct = ctx.persistence.literal_collection\n    return direct('other_app_tasks')",
    "def read(ctx):\n    direct = getattr(ctx.persistence, 'literal_collection')\n    return direct('other_app_tasks')",
    "from mozaiksai.core.runtime.persistence import app_data_from_context as data\ndef read(ctx):\n    return data(ctx, contract={'aliases': []})",
    "from mozaiksai.core.runtime.persistence import app_data_from_context as data\ndef read(ctx):\n    return data(ctx, app_root='/other/app')",
    "from mozaiksai.core.runtime.persistence import app_data_from_context as data\ndef read(ctx):\n    return data(ctx, **options)",
    "from mozaiksai.core.runtime.persistence.app_data import AppData as Data\ndef read(ctx):\n    return Data(aliases={'tasks': 'other_app_tasks'}, collection_resolver=ctx.persistence.literal_collection)",
    "def read(ctx):\n    identity = ctx.persistence.principal\n    identity.__dict__['user_id'] = 'victim'",
    "def read(ctx):\n    identity = ctx.persistence.principal\n    override = object.__setattr__\n    override(identity, 'user_id', 'victim')",
    "def escape(handle):\n    setattr(handle, '_principal', victim)",
    "from mozaiksai.core.runtime.persistence import app_data_from_context\ndef read(ctx):\n    rows = app_data_from_context(ctx).collection('billing.subscriptions')\n    cursor = rows.find({}).sort('name').skip(1).limit(4)\n    return cursor._GuardedAliasCursor__cursor",
    "from mozaiksai.core.runtime.persistence import app_data_from_context\ndef read(ctx):\n    rows = app_data_from_context(ctx).collection('billing.subscriptions')\n    cursor = rows.aggregate([])\n    return vars(cursor)",
    "import mozaiksai.core.core_config as config\nconnectors = [config.get_mongo_client]\nclient = connectors[0]()",
    "class Repo:\n    def _collection(self, ctx):\n        return ctx.persistence.collection('tasks', 'tasks')\n    async def read(self, ctx):\n        rows = self._collection(ctx)\n        return rows._collection",
])
def test_scanner_rejects_private_raw_and_aliased_persistence_escapes(source):
    findings = scan_module_persistence({PATH: source})
    assert findings, source
    assert all(item.startswith(PATH + ":") for item in findings)
    assert any(item in scan_generated_bundle({PATH: source}) for item in findings)


def test_scanner_accepts_injected_collections_and_repo_local_helper():
    source = """
class Repo:
    async def _collection(self, ctx):
        return ctx.persistence.collection('tasks', 'tasks')

    async def count(self, ctx):
        collection = await self._collection(ctx)
        return await collection.aggregate([{'$group': {'_id': None, 'count': {'$sum': 1}}}])
"""
    assert scan_module_persistence({PATH: source}) == []


def test_scanner_does_not_treat_comments_or_strings_as_database_access():
    assert scan_module_persistence({PATH: '# never use motor\nmessage = "MongoPersistenceContext and ._collection"'}) == []


def test_context_type_import_cannot_be_used_to_construct_through_new():
    source = "from mozaiksai.core.runtime.composition.module_context import ModuleContext\nctx = ModuleContext.__new__(ModuleContext)"
    assert scan_module_persistence({PATH: source})


def test_scanner_accepts_declared_app_data_aliases_without_namespace_overrides():
    source = """
from mozaiksai.core.runtime.persistence import app_data_from_context as data
def rows(ctx):
    return data(ctx).collection('billing.subscriptions')
def records(ctx):
    return data(ctx=ctx).collection('billing.subscriptions')
"""
    assert scan_module_persistence({PATH: source}) == []


def test_account_data_protocol_has_no_raw_client_exemption():
    path = "modules/tasks/backend/account_data_handler.py"
    assert scan_module_persistence({path: "from motor.motor_asyncio import AsyncIOMotorClient"})
    assert scan_module_persistence({path: "async def export_user_data(db, user_id):\n    return await db['tasks'].find_one({'user_id': user_id})"}) == []


def _startup(entrypoint="backend.poller:Worker"):
    return {"modules/tasks/runtime_extensions.yaml": f"""
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: startup_service
    entrypoint: {entrypoint}
"""}


@pytest.mark.parametrize("source", [
    "from .poller import Worker\nworker = Worker()",
    "from app.modules.tasks.backend.poller import Worker\nworker = Worker()",
    "from modules.tasks.backend import poller\nworker = poller.Worker()",
    "import app.modules.tasks.backend.poller as worker\ninstance = worker.Worker()",
    "from importlib import import_module\nworker = import_module('app.modules.tasks.backend.poller')",
])
def test_request_code_cannot_import_declared_startup_implementation(source):
    files = {**_startup(), "modules/tasks/backend/poller.py": "class Worker:\n    pass"}
    assert scan_module_persistence(files) == []
    files[PATH] = source
    assert any("cannot import a startup service" in error for error in scan_module_persistence(files))


@pytest.mark.parametrize("driver", ["motor.motor_asyncio", "pymongo"])
def test_startup_declaration_never_authorizes_raw_driver_imports(driver):
    files = {**_startup(), "modules/tasks/backend/poller.py": f"import {driver}"}
    assert any("raw Mongo driver imports" in error for error in scan_module_persistence(files))


def test_checked_in_module_templates_use_supported_persistence_boundaries():
    root = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"
    checked = 0
    for template_root in root.glob("*/templates"):
        files = {
            path.relative_to(template_root).as_posix(): path.read_text(encoding="utf-8")
            for path in (template_root / "modules").rglob("*") if path.suffix in {".py", ".yaml"}
        }
        checked += len(files)
        assert scan_module_persistence(files) == [], template_root
    assert checked > 0


@pytest.mark.parametrize("filename", ["repo", "handler", "service", "account_data_handler"])
def test_startup_declaration_cannot_exempt_request_backend_layers(filename):
    files = {**_startup(f"backend.{filename}:Worker"), f"modules/tasks/backend/{filename}.py": "import motor"}
    assert scan_module_persistence(files)


def test_startup_declaration_never_authorizes_private_persistence_escape():
    files = {**_startup(), "modules/tasks/backend/poller.py": "def read(ctx):\n    return ctx.persistence._client_handle()"}
    assert scan_module_persistence(files)
