"""A pack template is untrusted text: it renders to plan an install, before anyone approves it."""

from __future__ import annotations

import datetime
import os
from pathlib import Path, PurePosixPath

import pytest
from jinja2 import StrictUndefined, Template, TemplateError
from jinja2.sandbox import SecurityError

from factory_app.workflows.AppGenerator.tools import pack_template_rendering as rendering
from factory_app.workflows.AppGenerator.tools import (
    resolve_managed_capability_templates as resolver,
)
from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
    CommunityComponentLifecycleError,
)
from factory_app.workflows.AppGenerator.tools.community_component_sources import (
    PackSource,
)
from factory_app.workflows.AppGenerator.tools.community_component_workspace import (
    apply_component_install,
    apply_workspace_upgrade,
    plan_component_install,
    plan_workspace_upgrade,
    verify_installed_components,
)
from tests.test_pack_lifecycle_existing_app import (
    _generator_pack,
    _materialize,
    _ordinary_app,
    _pack,
    _tree,
    _version_two,
)

BUILD_CONTEXT = Path(resolver.__file__).parents[3] / "build_context"
MARKER = "MOZAIKS_TEMPLATE_ESCAPED"
RUN = f".environ.__setitem__('{MARKER}', 'yes')"
WRITE = "open(OUTSIDE, 'w').close()"

# Each tries to run code, read a Python object, reach another file, or exhaust the host.
EXPLOITS = {
    "cycler-globals": "{{ cycler.__init__.__globals__.os" + RUN + " }}",
    "joiner-globals": "{{ joiner.__init__.__globals__.os" + RUN + " }}",
    "namespace-globals": "{{ namespace.__init__.__globals__.os" + RUN + " }}",
    "lipsum-globals": "{{ lipsum.__globals__.os" + RUN + " }}",
    "dict-global": "{{ dict.__base__.__subclasses__() }}",
    "subclasses": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "subscript-class": "{{ ''['__class__'] }}",
    "attr-filter": "{{ ''|attr('__class__') }}",
    "attr-filter-built-name": "{{ ''|attr('__cl' ~ 'ass__') }}",
    "map-attribute-used": "{{ ['']|map(attribute='__class__')|map('string')|list }}",
    "sort-attribute": "{{ [pack_id]|sort(attribute='__class__.__mro__')|list }}",
    "sum-attribute": "{{ [pack_id]|sum(attribute='__class__') }}",
    "selectattr": "{{ [pack_id]|selectattr('__class__')|list }}",
    "format-string": "{{ '{0.__class__.__init__.__globals__}'.format(pack_id) }}",
    "format-map": "{{ '{x.__class__}'.format_map({'x': pack_id}) }}",
    "format-indirect": "{% set f = ['{0.__class__}'.format]|map('string')|first %}{{ f }}",
    "format-bomb": "{{ '{:>9999999999}'.format(pack_id) }}",
    "format-filter": "{{ '%s'|format(pack_id) }}",
    "printf": "{{ '%0999999999d' % 1 }}",
    "macro-globals": "{% macro m() %}{% endmacro %}{{ m.__globals__ }}",
    "macro-caller": "{% macro m() %}{{ caller.__func__ }}{% endmacro %}{% call m() %}{% endcall %}",
    "self-reference": "{{ self.__init__.__globals__ }}",
    "block-globals": "{% block b %}{{ self.b.__globals__ }}{% endblock %}",
    "context-object": "{% set c = self._TemplateReference__context %}{{ c.environment }}",
    "loop-object": "{% for x in [1] %}{{ loop.__class__.__init__.__globals__ }}{% endfor %}",
    "undefined-object": "{{ missing.__init__.__globals__ }}",
    "generator-frame": "{{ ([1]|map('string')).gi_frame.f_globals }}",
    "data-mutation": "{% set _ = capability_tags.append('x') %}",
    "builtin-open": "{{ open('x', 'w') }}",
    "outside-write": "{{ lipsum.__globals__['os'].open('OUTSIDE', 66) }}",
    "outside-write-builtins": "{{ cycler.__init__.__globals__.__builtins__." + WRITE + " }}",
    "include": "{% include 'config.yaml' %}",
    "import": "{% import 'config.yaml' as c %}",
    "from-import": "{% from 'config.yaml' import x %}",
    "extends": "{% extends 'config.yaml' %}",
    "range-bomb": "{% for i in range(10 ** 9) %}x{% endfor %}",
    "repeat-bomb": "{{ 'x' * 10000000000 }}",
    "repeat-bomb-reversed": "{{ 10000000000 * [pack_id] }}",
    "power-bomb": "{{ 9 ** 999999999 }}",
    "power-chain": "{{ (9 ** 1000) ** 1000 }}",
    "padding-bomb": "{{ 'x'|center(10000000000) }}",
    "method-padding-bomb": "{{ 'x'.ljust(10000000000) }}",
    "indent-bomb": "{{ pack_id|indent(10000000000, true) }}",
    "batch-bomb": "{{ [1]|batch(1000000000, 0)|list }}",
    "output-bomb": "{% for a in text %}{% for b in text %}{% for c in text %}{{ text }}{% endfor %}{% endfor %}{% endfor %}",
    "zero-division": "{{ 1 / 0 }}",
}


@pytest.fixture(autouse=True)
def _no_marker():
    os.environ.pop(MARKER, None)
    yield
    escaped = os.environ.pop(MARKER, None)
    assert escaped is None, "a pack template ran code"


def _template(text: str, outside: Path) -> str:
    return text.replace("OUTSIDE", outside.as_posix())


def _poison(pack: Path, text: str, outside: Path) -> Path:
    target = pack / "templates/services/note.txt.j2"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{% set text = 'x' * 300 %}" + _template(text, outside), encoding="utf-8")
    return pack


@pytest.mark.parametrize("name", EXPLOITS)
def test_a_hostile_template_is_refused_by_the_renderer(tmp_path, name):
    outside = tmp_path / "escaped"
    variables = {"pack_id": "p", "text": "x" * 300, "capability_tags": ["a"]}
    with pytest.raises(resolver._TEMPLATE_FAILURES):
        rendering.render_pack_template(_template(EXPLOITS[name], outside), variables)
    assert variables == {"pack_id": "p", "text": "x" * 300, "capability_tags": ["a"]}
    assert not outside.exists()


@pytest.mark.parametrize(
    "source", ["{{ ['']|map(attribute='__class__')|list }}", "{{ [pack_id]|groupby('__class__')|list }}"],
)
def test_filters_do_not_reach_attributes_by_name(source):
    assert "class" not in rendering.render_pack_template(source, {"pack_id": "p"})


@pytest.mark.parametrize(
    "name",
    ["cycler-globals", "joiner-globals", "lipsum-globals", "subclasses", "attr-filter", "format-string", "format-indirect",
     "outside-write", "outside-write-builtins", "include", "repeat-bomb", "output-bomb", "zero-division"],
)
@pytest.mark.parametrize("operation", ["install", "upgrade", "generator-upgrade"])
def test_planning_and_applying_a_hostile_pack_runs_nothing(tmp_path, operation, name):
    roots = _ordinary_app(tmp_path / "shop")
    outside = tmp_path / "escaped"
    if operation == "install":
        pack, plan, apply = _pack(tmp_path), plan_component_install, apply_component_install
    elif operation == "upgrade":
        apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        pack, plan, apply = _version_two(tmp_path), plan_workspace_upgrade, apply_workspace_upgrade
    else:
        _materialize(roots, _generator_pack(tmp_path, "1.0.0", modules=("ledger",)))
        pack = _generator_pack(tmp_path, "2.0.0", modules=("ledger",))
        plan, apply = plan_workspace_upgrade, apply_workspace_upgrade
    source = PackSource(directory=_poison(pack, EXPLOITS[name], outside))
    before = _tree(tmp_path / "shop")
    for attempt in (plan, apply):
        with pytest.raises(CommunityComponentLifecycleError, match="could not render"):
            attempt(source=source, roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert not outside.exists()


def test_verify_never_renders_an_installed_source(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    pack = _pack(tmp_path)
    apply_component_install(source=PackSource(directory=pack), roots=roots)
    outside = tmp_path / "escaped"
    _poison(pack, EXPLOITS["cycler-globals"] + EXPLOITS["outside-write"], outside)
    assert verify_installed_components(roots=roots)["status"] == "clean"
    assert not outside.exists()


def _reference(source: str, variables) -> str:
    """The unrestricted rendering this module replaced."""
    return Template(source, undefined=StrictUndefined).render(**variables)


READINESS = {
    "readiness_profile": "lean & 'quoted' <tag>", "evidence_mode": "live", "evidence_ledger_path": "docs/evidence.json",
    "launch_check_command": "python check.py --json", "monetization_check_command": "python pay.py \"x\"",
    "local_launcher": "scripts/launch.ps1", "build_timestamp": "2026-01-01T00:00:00Z",
}


@pytest.mark.parametrize("context", [READINESS, {"readiness_profile": "local"}], ids=["full", "defaults"])
def test_every_shipped_pack_renders_the_same_bytes(monkeypatch, context):
    packs = sorted(path.parent for path in BUILD_CONTEXT.glob("*/context.yaml") if (path.parent / "templates").is_dir())
    assert len(packs) > 5
    rendered = {}
    for renderer in (rendering.render_pack_template, _reference):
        monkeypatch.setattr(resolver, "render_pack_template", renderer)
        rendered[renderer] = {
            pack.name: resolver.resolve_templates_for_pack(pack, pack.name, context_variables=context) for pack in packs
        }
    assert rendered[rendering.render_pack_template] == rendered[_reference]
    templates = [path for pack in packs for path in pack.rglob("*.j2")]
    assert templates and all(rendered[_reference][path.relative_to(BUILD_CONTEXT).parts[0]] for path in templates)


@pytest.mark.parametrize(
    "source",
    [
        "plain text\n\n", "{{ name }}\n", "{{ name | upper | replace('A', 'b') | tojson }}", "{{ missing_name | default('x') }}",
        "{% for key, value in table.items() %}{{ loop.index }}:{{ key }}={{ value }}{{ ',' if not loop.last }}{% endfor %}",
        "{% for row in rows if row.on %}{{ row.label.strip() }}{% else %}none{% endfor %}", "{{ rows | map(attribute='label') | join('-') }}",
        "{% set total = 3 * 4 %}{{ total }} {{ 7 % 4 }} {{ 2 ** 8 }} {{ '-' * 5 }} {{ (1, 2) }} {{ when }} {{ where }} {{ flags }}",
        "{% macro cell(value) %}[{{ value }}]{% endmacro %}{{ cell(name) }}{{ cell(table['a']) }}", "{# comment #}{% raw %}{{ kept }}{% endraw %}",
        "{% if name is defined and name.startswith('a') %}yes{% endif %}", "{{ rows | selectattr('on') | list | length }}",
        "{{ table | dictsort }} {{ rows | sort(attribute='label') | first }} {{ nested.deep[0].value }}",
    ],
)
def test_ordinary_templates_render_as_before(source):
    variables = {
        "name": "alpha & <b>", "table": {"a": 1, "b": [1, 2]}, "rows": [{"label": " z ", "on": True}, {"label": "y", "on": False}],
        "when": datetime.date(2026, 1, 2), "where": PurePosixPath("a/b"), "flags": (True, None, 1.5), "nested": {"deep": [{"value": "v"}]},
    }
    assert rendering.render_pack_template(source, variables) == _reference(source, variables)


def test_only_plain_data_reaches_a_template():
    class Secret:
        token = "secret"

        def __str__(self) -> str:
            return "secret"

    class Text(str):
        leak = Secret()

    variables = {
        "thing": Secret(), "call": os.getenv, "module": os, "kind": dict, "rows": [1, Secret()], "table": {"a": Secret(), "b": 2},
        "text": Text("plain"),
    }
    for name in ("thing", "call", "module", "kind"):
        with pytest.raises(TemplateError, match="undefined"):
            rendering.render_pack_template("{{ " + name + " }}", variables)
    assert rendering.render_pack_template("{{ rows }} {{ table }} {{ text }}", variables) == "[1] {'b': 2} plain"
    with pytest.raises(TemplateError):
        rendering.render_pack_template("{{ text.leak }}", variables)


def test_the_template_environment_offers_nothing_but_filters():
    environment = rendering._ENVIRONMENT
    assert environment.globals == {}
    assert environment.loader is None and environment.extensions == {}
    assert environment.undefined is StrictUndefined
    assert not {"attr", "format", "center", "indent", "batch", "slice", "pprint", "xmlattr"} & environment.filters.keys()


def test_templates_and_their_output_are_bounded(monkeypatch):
    monkeypatch.setattr(rendering, "MAX_TEMPLATE_CHARS", 20)
    with pytest.raises(SecurityError, match="longer than"):
        rendering.render_pack_template("x" * 21, {})
    monkeypatch.setattr(rendering, "MAX_RENDERED_CHARS", 5)
    with pytest.raises(SecurityError, match="renders more"):
        rendering.render_pack_template("{{ t }}{{ t }}", {"t": "xxx"})
