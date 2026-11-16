"""Extract source facts without importing or executing workspace code."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

_GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "css": ("tree_sitter_css", "language"),
}
_FALLBACK_PARSERS = {
    "python": "python_ast",
    "javascript": "regex",
    "typescript": "regex",
    "tsx": "regex",
    "css": "regex",
}
_JS_IMPORT = re.compile(
    r"""^\s*(?:import\s+(?:[^;\n]*?\s+from\s+)?|export\s+[^;\n]*?\s+from\s+)['\"]([^'\"]+)['\"]""",
    re.MULTILINE,
)
_JS_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:(function|class|interface|type|enum)\s+([\w$]+)|"
    r"(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[\w$]+)\s*=>)",
    re.MULTILINE,
)
_CSS_IMPORT = re.compile(r"""^\s*@import\s+(?:url\(\s*)?['\"]?([^\s'\"();]+)""", re.MULTILINE)
_JS_DECLARATIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
_JS_FUNCTIONS = {"arrow_function", "function_expression", "generator_function"}
_PY_COMPREHENSIONS = {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}
_MAX_SOURCE_BYTES = 500_000
_MAX_SYNTAX_DEPTH = 256
_MAX_SYNTAX_NODES = 100_000


class _SyntaxLimitError(Exception):
    """A source fact exceeds the bounded syntax traversal."""


@dataclass
class ExtractedSymbol:
    name: str
    kind: str
    line: int | None = None
    end_line: int | None = None
    qualified_name: str | None = None
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedReference:
    target: str
    kind: str
    line: int | None = None
    caller: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceImport:
    target: str
    line: int | None = None
    scope: str | None = None
    local_name: str | None = None
    member: str | None = None
    module_tail: str | None = None
    commonjs: bool = False
    builtin_require: bool = False


@dataclass
class SourceSyntax:
    parser: str
    parse_status: str = "parsed"
    parse_error_count: int = 0
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    imports: list[SourceImport] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    local_bindings: set[tuple[str | None, str]] = field(default_factory=set)
    exports: dict[str, str] = field(default_factory=dict)


def parser_status() -> dict[str, Any]:
    languages = {}
    for language, (module_name, _) in _GRAMMARS.items():
        available = _tree_sitter_parser(language) is not None
        fallback = _FALLBACK_PARSERS[language]
        languages[language] = {
            "tree_sitter_module": module_name,
            "tree_sitter_available": available,
            "fallback_parser": fallback,
            "active_parser": "tree_sitter" if available else fallback,
        }
    for language in ("yaml", "json"):
        languages[language] = {
            "tree_sitter_module": None,
            "tree_sitter_available": False,
            "active_parser": f"structured_{language}",
        }
    return {
        "tree_sitter_package_available": importlib.util.find_spec("tree_sitter") is not None,
        "languages": languages,
    }


def extract_source_syntax(language: str, content: str) -> SourceSyntax:
    parser = _tree_sitter_parser(language)
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_SOURCE_BYTES:
        return SourceSyntax("tree_sitter" if parser else _FALLBACK_PARSERS[language], parse_status="partial", parse_error_count=1)
    if parser is not None:
        try:
            root = parser.parse(encoded).root_node
        except (ValueError, OverflowError, RecursionError):
            return SourceSyntax("tree_sitter", parse_status="failed", parse_error_count=1)
        return _TreeSitterExtractor(language).extract(root)
    if language == "python":
        syntax = SourceSyntax("python_ast")
        try:
            _PythonAstExtractor(syntax).visit(ast.parse(content))
        except SyntaxError:
            return SourceSyntax("python_ast", parse_status="failed", parse_error_count=1)
        except (RecursionError, _SyntaxLimitError):
            syntax.parse_status = "partial"
            syntax.parse_error_count += 1
        return syntax
    return _extract_fallback(language, content)


@lru_cache(maxsize=len(_GRAMMARS))
def _tree_sitter_language(language: str) -> Any | None:
    module, factory = _GRAMMARS[language]
    try:
        from tree_sitter import Language

        return Language(getattr(importlib.import_module(module), factory)())
    except (ImportError, AttributeError, TypeError, ValueError, OSError):
        return None


def _tree_sitter_parser(language: str) -> Any | None:
    grammar = _tree_sitter_language(language)
    if grammar is None:
        return None
    from tree_sitter import Parser

    try:
        return Parser(grammar)
    except (TypeError, ValueError):
        return None


class _TreeSitterExtractor:
    def __init__(self, language: str) -> None:
        self.language = language
        self.syntax = SourceSyntax("tree_sitter")
        self.scope_parents: dict[str, str | None] = {}

    def extract(self, root: Any) -> SourceSyntax:
        pending: list[tuple[Any, str | None, str | None, int]] = [(root, None, None, 0)]
        visited = 0
        while pending:
            node, parent, parent_kind, depth = pending.pop()
            visited += 1
            if visited > _MAX_SYNTAX_NODES:
                self.syntax.parse_error_count += 1
                break
            if depth > _MAX_SYNTAX_DEPTH:
                self.syntax.parse_error_count += 1
                continue
            if node.is_error or node.is_missing:
                self.syntax.parse_error_count += 1
                continue
            try:
                if self.language == "python":
                    symbol = self._python_facts(node, parent, parent_kind)
                elif self.language == "css":
                    self._css_import(node)
                    symbol = None
                else:
                    symbol = self._javascript_facts(node, parent)
            except _SyntaxLimitError:
                self.syntax.parse_error_count += 1
                continue
            if symbol is not None:
                self.syntax.symbols.append(symbol)
                if symbol.qualified_name is not None:
                    self.scope_parents[symbol.qualified_name] = symbol.parent
            children = node.children if node.has_error else node.named_children
            if symbol is not None and self.language == "python":
                if node.type in _PY_COMPREHENSIONS:
                    pending.extend((child, symbol.qualified_name, symbol.kind, depth + 1) for child in reversed(children))
                    continue
                body = node.child_by_field_name("body")
                parameters = node.child_by_field_name("parameters")
                try:
                    self._add_bindings(parameters, symbol.qualified_name)
                except _SyntaxLimitError:
                    self.syntax.parse_error_count += 1
                    continue
                pending.extend(
                    (child, symbol.qualified_name, symbol.kind, depth + 1) if child == body else (child, parent, parent_kind, depth + 1)
                    for child in reversed(children)
                )
            elif self.language == "python" and node.type == "for_in_clause" and node.parent.type in _PY_COMPREHENSIONS:
                first_clause = next(child for child in node.parent.named_children if child.type == "for_in_clause")
                outer_iterable = node.child_by_field_name("right") if node == first_clause else None
                pending.extend(
                    (child, self.scope_parents.get(parent) if parent else None, None, depth + 1)
                    if child == outer_iterable else (child, parent, parent_kind, depth + 1)
                    for child in reversed(children)
                )
            else:
                if symbol is not None:
                    parent, parent_kind = symbol.qualified_name, symbol.kind
                pending.extend((child, parent, parent_kind, depth + 1) for child in reversed(children))

        self._resolve_require_bindings()
        default_export = self.syntax.exports.get("default")
        for symbol in self.syntax.symbols:
            if symbol.parent is None and symbol.name == default_export:
                symbol.metadata["default_export"] = True
        if root.has_error or self.syntax.parse_error_count:
            self.syntax.parse_status = "partial"
            self.syntax.parse_error_count = max(1, self.syntax.parse_error_count)
        return self.syntax

    def _resolve_require_bindings(self) -> None:
        bindings = set(self.syntax.local_bindings)
        bindings.update((item.parent, item.name) for item in self.syntax.symbols)
        bindings.update((item.scope, item.local_name) for item in self.syntax.imports if item.local_name)

        def shadowed(scope: str | None) -> bool:
            while scope:
                if (scope, "require") in bindings:
                    return True
                scope = scope.rpartition(".")[0]
            return (None, "require") in bindings

        imports = []
        for item in self.syntax.imports:
            if item.builtin_require and shadowed(item.scope):
                if item.local_name:
                    self.syntax.local_bindings.add((item.scope, item.local_name))
            else:
                imports.append(item)
        self.syntax.imports = imports
        references = []
        for reference in self.syntax.references:
            if reference.metadata.pop("builtin_require", False) and not shadowed(reference.caller):
                continue
            references.append(reference)
        self.syntax.references = references

    def _python_facts(
        self, node: Any, parent: str | None, parent_kind: str | None,
    ) -> ExtractedSymbol | None:
        if node.type in {"import_statement", "import_from_statement"}:
            module = _text(node.child_by_field_name("module_name"))
            for name in node.children_by_field_name("name"):
                imported = _text(name.child_by_field_name("name")) if name.type == "aliased_import" else _text(name)
                alias = _text(name.child_by_field_name("alias"))
                if imported:
                    self.syntax.imports.append(_python_import(module, imported, alias, parent, _line(node)))
            if module and any(child.type == "wildcard_import" for child in node.named_children):
                self.syntax.imports.append(SourceImport(module, _line(node), parent))
        if node.type == "call":
            self._reference(_python_target(node.child_by_field_name("function")), node, parent)
        if node.type in {"assignment", "augmented_assignment", "for_statement", "for_in_clause"}:
            self._add_bindings(node.child_by_field_name("left"), parent)
        if node.type == "as_pattern":
            self._add_bindings(node.child_by_field_name("alias"), parent)
        if node.type == "lambda":
            return self._symbol(node, _anonymous_name(node, "lambda"), "function", parent)
        if node.type in _PY_COMPREHENSIONS:
            return self._symbol(node, _anonymous_name(node, "comprehension"), "function", parent)
        if node.type not in {"function_definition", "class_definition"}:
            return None
        name = _text(node.child_by_field_name("name"))
        if not name:
            return None
        kind = "class" if node.type == "class_definition" else _python_function_kind(parent_kind)
        decorators = []
        if node.parent is not None and node.parent.type == "decorated_definition":
            for decorator in node.parent.named_children:
                if decorator.type == "decorator" and decorator.named_children:
                    value = decorator.named_children[0]
                    if value.type == "call":
                        value = value.child_by_field_name("function")
                    if target := _python_target(value):
                        decorators.append(target)
        metadata: dict[str, Any] = {"decorators": list(dict.fromkeys(decorators))}
        if kind != "class":
            metadata["async"] = any(child.type == "async" for child in node.children)
        return self._symbol(node, name, kind, parent, metadata)

    def _javascript_facts(self, node: Any, parent: str | None) -> ExtractedSymbol | None:
        if node.type in {"import_statement", "export_statement"}:
            self._javascript_import_or_export(node, parent)
        elif node.type in {"call_expression", "new_expression"}:
            target_node = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            target = _javascript_target(target_node)
            if source := _javascript_loaded_module(node):
                self.syntax.imports.append(SourceImport(source, _line(node), parent, builtin_require=target == "require"))
            elif target not in {"import", "require"}:
                self._reference(target, node, parent)
            if target == "require":
                self._reference(target, node, parent)
                self.syntax.references[-1].metadata["builtin_require"] = True
        elif node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
            target = _javascript_target(node.child_by_field_name("name"))
            if target and (target[0].isupper() or "." in target):
                self._reference(target, node, parent, kind="reference")
        elif node.type == "assignment_expression" and parent is None:
            self._commonjs_export(node)

        name_node = node.child_by_field_name("name")
        kind = _JS_DECLARATIONS.get(node.type)
        if node.type in _JS_FUNCTIONS:
            enclosing = node.parent
            function_scope = parent
            if enclosing is not None and enclosing.type == "export_statement":
                function_scope = _qualify(parent, _text(name_node) or "default")
            elif enclosing is None or enclosing.type not in {"variable_declarator", "field_definition", "public_field_definition"}:
                name = _text(name_node) or _anonymous_name(node, "function")
                function_scope = _qualify(parent, name)
                self._add_bindings(node.child_by_field_name("parameters"), function_scope)
                self._add_bindings(node.child_by_field_name("parameter"), function_scope)
                return self._symbol(node, name, "function", parent)
            self._add_bindings(node.child_by_field_name("parameters"), function_scope)
            self._add_bindings(node.child_by_field_name("parameter"), function_scope)
        elif kind in {"function", "method"} and name_node is not None:
            self._add_bindings(node.child_by_field_name("parameters"), _qualify(parent, _text(name_node)))
        if node.type in {"variable_declarator", "field_definition", "public_field_definition"}:
            value = node.child_by_field_name("value")
            if value is not None and value.type in _JS_FUNCTIONS:
                kind = "function" if node.type == "variable_declarator" else "method"
                name_node = name_node or node.child_by_field_name("property")
            if node.type == "variable_declarator" and kind is None:
                is_require = (
                    value is not None and value.type == "call_expression"
                    and self._javascript_require_bindings(name_node, value, parent)
                )
                if not is_require:
                    self._add_bindings(name_node, parent)
        if kind and name_node is not None and name_node.type in {"identifier", "type_identifier", "property_identifier", "private_property_identifier"}:
            return self._symbol(node, _text(name_node), kind, parent)
        if node.type in _JS_FUNCTIONS | {"class"} and node.parent is not None and node.parent.type == "export_statement":
            if _javascript_default_export(node.parent):
                name = _text(name_node) or "default"
                self.syntax.exports["default"] = name
                return self._symbol(node, name, "class" if node.type == "class" else "function", parent)
        return None

    def _javascript_import_or_export(self, node: Any, parent: str | None) -> None:
        source = _string_literal(node.child_by_field_name("source"))
        if node.type == "export_statement":
            if source:
                self.syntax.imports.append(SourceImport(source, _line(node), parent))
            elif parent is None:
                self._javascript_exports(node)
            return
        bindings: list[tuple[str | None, str | None]] = []
        commonjs = False
        for child in node.named_children:
            if child.type == "import_require_clause":
                source = _string_literal(child.child_by_field_name("source"))
                commonjs = True
                identifier = next((part for part in child.named_children if part.type == "identifier"), None)
                if identifier is not None:
                    bindings.append((_text(identifier), None))
            elif child.type == "import_clause":
                for item in child.named_children:
                    if item.type == "identifier":
                        bindings.append((_text(item), "default"))
                    elif item.type == "namespace_import" and item.named_children:
                        bindings.append((_text(item.named_children[-1]), None))
                    elif item.type == "named_imports":
                        for specifier in item.named_children:
                            if specifier.type != "import_specifier":
                                continue
                            member = _text(specifier.child_by_field_name("name"))
                            alias = _text(specifier.child_by_field_name("alias"))
                            if member:
                                bindings.append((alias or member, member))
        if source:
            self.syntax.imports.extend(
                SourceImport(source, _line(node), parent, local, member, commonjs=commonjs)
                for local, member in bindings or [(None, None)]
            )

    def _javascript_exports(self, node: Any) -> None:
        declaration = node.child_by_field_name("declaration")
        if _javascript_default_export(node):
            name = _text(declaration.child_by_field_name("name")) if declaration is not None else _javascript_target(node.child_by_field_name("value"))
            if name:
                self.syntax.exports["default"] = name
        elif declaration is not None:
            if name := _text(declaration.child_by_field_name("name")):
                self.syntax.exports[name] = name
            for child in declaration.named_children:
                if child.type == "variable_declarator":
                    for name in _pattern_names(child.child_by_field_name("name")):
                        self.syntax.exports[name] = name
        else:
            for clause in node.named_children:
                if clause.type == "export_clause":
                    for item in clause.named_children:
                        if item.type != "export_specifier":
                            continue
                        name = _text(item.child_by_field_name("name"))
                        alias = _text(item.child_by_field_name("alias"))
                        if name:
                            self.syntax.exports[alias or name] = name

    def _javascript_require_bindings(self, name: Any, call: Any, parent: str | None) -> bool:
        if name is None or _javascript_target(call.child_by_field_name("function")) != "require":
            return False
        source = _javascript_loaded_module(call)
        if not source:
            return False
        if name.type == "identifier":
            self.syntax.imports.append(SourceImport(source, _line(call), parent, _text(name), commonjs=True, builtin_require=True))
            return True
        elif name.type == "object_pattern":
            for item in name.named_children:
                local: str | None
                if item.type == "shorthand_property_identifier_pattern":
                    local = member = _text(item)
                elif item.type == "pair_pattern":
                    local = _javascript_target(item.child_by_field_name("value"))
                    member = _text(item.child_by_field_name("key"))
                else:
                    continue
                if local and member:
                    self.syntax.imports.append(SourceImport(source, _line(call), parent, local, member, commonjs=True, builtin_require=True))
            return True
        return False

    def _commonjs_export(self, node: Any) -> None:
        target = _javascript_target(node.child_by_field_name("left")) or ""
        right = node.child_by_field_name("right")
        if right is None:
            return
        if target == "module.exports":
            if value := _javascript_target(right):
                self.syntax.exports["default"] = value
            elif right.type == "object":
                for item in right.named_children:
                    if item.type == "shorthand_property_identifier":
                        self.syntax.exports[_text(item)] = _text(item)
                    elif item.type == "pair":
                        key = _text(item.child_by_field_name("key"))
                        value = _javascript_target(item.child_by_field_name("value"))
                        if key and value:
                            self.syntax.exports[key] = value
        elif target.startswith(("exports.", "module.exports.")):
            key = target.rsplit(".", 1)[-1]
            if value := _javascript_target(right):
                self.syntax.exports[key] = value

    def _css_import(self, node: Any) -> None:
        if node.type == "import_statement" and node.named_children:
            value = node.named_children[0]
            if value.type == "call_expression" and len(value.named_children) == 2:
                function, arguments = value.named_children
                if _text(function) == "url" and arguments.named_children:
                    value = arguments.named_children[0]
            source = _string_literal(value) or (_text(value) if value.type == "plain_value" else None)
            if not source and node.has_error:
                match = _CSS_IMPORT.match(_text(node))
                source = match.group(1) if match else None
            if source:
                self.syntax.imports.append(SourceImport(source, _line(node)))

    def _symbol(
        self, node: Any, name: str, kind: str, parent: str | None, metadata: dict[str, Any] | None = None,
    ) -> ExtractedSymbol:
        return ExtractedSymbol(
            name, kind, _line(node), node.end_point.row + 1, _qualify(parent, name), parent,
            {"parser": self.syntax.parser, **(metadata or {})},
        )

    def _reference(self, target: str | None, node: Any, parent: str | None, *, kind: str = "call") -> None:
        if target:
            self.syntax.references.append(
                ExtractedReference(target, kind, _line(node), parent, {"parser": self.syntax.parser})
            )

    def _add_bindings(self, node: Any, parent: str | None) -> None:
        self.syntax.local_bindings.update((parent, name) for name in _pattern_names(node))


class _PythonAstExtractor(ast.NodeVisitor):
    def __init__(self, syntax: SourceSyntax) -> None:
        self.syntax = syntax
        self.scope: list[tuple[str, str]] = []
        self.depth = 0
        self.visited = 0

    def visit(self, node: ast.AST) -> Any:
        self.visited += 1
        if self.depth >= _MAX_SYNTAX_DEPTH or self.visited > _MAX_SYNTAX_NODES:
            raise _SyntaxLimitError
        self.depth += 1
        try:
            return super().visit(node)
        finally:
            self.depth -= 1

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        name = f"<lambda@{node.lineno}:{node.col_offset}>"
        parent = self._caller()
        self.syntax.symbols.append(ExtractedSymbol(
            name, "function", node.lineno, node.end_lineno, _qualify(parent, name), parent, {"parser": "python_ast"},
        ))
        self.scope.append((name, "function"))
        args = node.args
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
        self.syntax.local_bindings.update((self._caller(), param.arg) for param in parameters if param)
        self.visit(node.body)
        self.scope.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.syntax.local_bindings.add((self._caller(), node.name))
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
        self.visit(node.generators[0].iter)
        name = f"<comprehension@{node.lineno}:{node.col_offset}>"
        parent = self._caller()
        self.syntax.symbols.append(ExtractedSymbol(
            name, "function", node.lineno, node.end_lineno, _qualify(parent, name), parent, {"parser": "python_ast"},
        ))
        self.scope.append((name, "function"))
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]:
            self.visit(value)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.syntax.imports.append(_python_import("", alias.name, alias.asname, self._caller(), node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            self.syntax.imports.append(_python_import(module, alias.name, alias.asname, self._caller(), node.lineno))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in [*node.bases, *node.keywords]:
            self.visit(base)
        self._visit_symbol(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if target := _ast_target(node.func):
            self.syntax.references.append(
                ExtractedReference(target, "call", node.lineno, self._caller(), {"parser": "python_ast"})
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.syntax.local_bindings.add((self._caller(), node.id))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._visit_symbol(node, _python_function_kind(self.scope[-1][1] if self.scope else None))

    def _visit_symbol(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        decorators = []
        for decorator in node.decorator_list:
            if name := _ast_target(decorator.func if isinstance(decorator, ast.Call) else decorator):
                decorators.append(name)
            self.visit(decorator)
        metadata: dict[str, Any] = {"parser": "python_ast", "decorators": list(dict.fromkeys(decorators))}
        if kind != "class":
            metadata["async"] = isinstance(node, ast.AsyncFunctionDef)
        parent = self._caller()
        self.syntax.symbols.append(
            ExtractedSymbol(node.name, kind, node.lineno, node.end_lineno, _qualify(parent, node.name), parent, metadata)
        )
        self.scope.append((node.name, kind))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
            self.syntax.local_bindings.update((self._caller(), param.arg) for param in parameters if param)
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def _caller(self) -> str | None:
        return ".".join(name for name, _ in self.scope) or None


def _python_import(module: str, imported: str, alias: str | None, scope: str | None, line: int) -> SourceImport:
    if module:
        return SourceImport(module, line, scope, alias or imported, imported) if imported != "*" else SourceImport(module, line, scope)
    root, _, tail = imported.partition(".")
    return SourceImport(imported, line, scope, alias or root, module_tail=None if alias else tail or None)


def _python_function_kind(parent_kind: str | None) -> str:
    return "method" if parent_kind == "class" else "function"


def _python_target(node: Any) -> str | None:
    parts: list[str] = []
    while node is not None:
        if len(parts) >= _MAX_SYNTAX_DEPTH:
            raise _SyntaxLimitError
        if node.type == "identifier":
            return ".".join([_text(node), *reversed(parts)])
        if node.type != "attribute":
            return None
        tail = _text(node.child_by_field_name("attribute"))
        if not tail:
            return None
        parts.append(tail)
        node = node.child_by_field_name("object")
    return None


def _ast_target(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        if len(parts) >= _MAX_SYNTAX_DEPTH:
            raise _SyntaxLimitError
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) else None


def _javascript_target(node: Any) -> str | None:
    parts: list[str] = []
    while node is not None:
        if len(parts) >= _MAX_SYNTAX_DEPTH:
            raise _SyntaxLimitError
        if node.type in {"identifier", "this", "import"}:
            return ".".join([_text(node), *reversed(parts)])
        if node.type != "member_expression":
            return None
        tail = _text(node.child_by_field_name("property"))
        if not tail:
            return None
        parts.append(tail)
        node = node.child_by_field_name("object")
    return None


def _javascript_loaded_module(node: Any) -> str | None:
    target = _javascript_target(node.child_by_field_name("function"))
    if target not in {"require", "import"}:
        return None
    args = node.child_by_field_name("arguments")
    if args is not None:
        first = next((child for child in args.named_children if child.type != "comment"), None)
        if first is not None and first.type == "template_string" and target == "import":
            if all(child.type == "string_fragment" for child in first.named_children):
                return _text(first)[1:-1] or None
        return _string_literal(first)
    return None


def _javascript_default_export(node: Any) -> bool:
    return any(child.type == "default" for child in node.children)


def _pattern_names(node: Any) -> list[str]:
    fields = {
        "assignment_pattern": "left",
        "default_parameter": "name",
        "typed_default_parameter": "name",
        "optional_parameter": "pattern",
        "required_parameter": "pattern",
        "pair_pattern": "value",
    }
    containers = {
        "parameters", "lambda_parameters", "formal_parameters", "typed_parameter", "list_splat_pattern", "dictionary_splat_pattern",
        "rest_pattern", "array_pattern", "object_pattern", "pattern_list", "tuple_pattern", "list_pattern",
        "as_pattern_target",
    }
    names = []
    pending = [(node, 0)] if node is not None else []
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > _MAX_SYNTAX_DEPTH or visited > _MAX_SYNTAX_NODES:
            raise _SyntaxLimitError
        if item.type in {"identifier", "shorthand_property_identifier_pattern"}:
            names.append(_text(item))
        elif field := fields.get(item.type):
            child = item.child_by_field_name(field)
            if child is not None:
                pending.append((child, depth + 1))
        elif item.type in containers:
            pending.extend((child, depth + 1) for child in reversed(item.named_children))
    return names


def _extract_fallback(language: str, content: str) -> SourceSyntax:
    syntax = SourceSyntax("regex", parse_status="partial")
    newlines = [index for index, char in enumerate(content) if char == "\n"]
    import_pattern = _CSS_IMPORT if language == "css" else _JS_IMPORT
    for match in import_pattern.finditer(content):
        syntax.imports.append(SourceImport(match.group(1), bisect_right(newlines, match.start(1)) + 1))
    if language != "css":
        for match in _JS_SYMBOL.finditer(content):
            kind, declaration, variable = match.groups()
            name = declaration or variable
            line = bisect_right(newlines, match.start(2 if declaration else 3)) + 1
            syntax.symbols.append(ExtractedSymbol(name, kind or "function", line, qualified_name=name, metadata={"parser": "regex"}))
    return syntax


def _qualify(parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _anonymous_name(node: Any, kind: str) -> str:
    return f"<{kind}@{_line(node)}:{node.start_point.column}>"


def _text(node: Any) -> str:
    return node.text.decode("utf-8").strip() if node is not None and node.text is not None else ""


def _line(node: Any) -> int:
    return int(node.start_point.row) + 1


def _string_literal(node: Any) -> str | None:
    if node is None or node.type not in {"string", "string_value"} or node.has_error:
        return None
    value = _text(node)
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0] and "\\" not in value:
        return value[1:-1] or None
    return None
