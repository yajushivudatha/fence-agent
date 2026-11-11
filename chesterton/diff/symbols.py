"""Map line ranges to their enclosing function or class with tree-sitter.

Grammars are imported on first use and files in other languages simply get no
symbol, which leaves them with line-level analysis.
"""

import importlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from tree_sitter import Language, Node, Parser

_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
}

_GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "java": ("tree_sitter_java", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
}

_JS_FUNCTIONS = {"function_declaration", "generator_function_declaration", "method_definition"}
_JS_TYPES = {"class_declaration"}
_C_FUNCTIONS = {"function_definition"}
_C_TYPES = {"struct_specifier", "union_specifier", "enum_specifier"}

_FUNCTION_NODES = {
    "python": {"function_definition"},
    "javascript": _JS_FUNCTIONS,
    "typescript": _JS_FUNCTIONS,
    "tsx": _JS_FUNCTIONS,
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
    "rust": {"function_item"},
    "c": _C_FUNCTIONS,
    "cpp": _C_FUNCTIONS,
}

_TYPE_NODES = {
    "python": {"class_definition"},
    "javascript": _JS_TYPES,
    "typescript": _JS_TYPES | {"abstract_class_declaration", "interface_declaration"},
    "tsx": _JS_TYPES | {"abstract_class_declaration", "interface_declaration"},
    "go": {"type_spec"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
    "rust": {"impl_item", "struct_item", "enum_item", "trait_item", "mod_item"},
    "c": _C_TYPES,
    "cpp": _C_TYPES | {"class_specifier", "namespace_definition"},
}

_FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function"}
_NAME_NODES = {
    "identifier",
    "field_identifier",
    "type_identifier",
    "qualified_identifier",
    "destructor_name",
    "operator_name",
}


@dataclass(frozen=True)
class Symbol:
    name: str
    start: int  # 1-based, inclusive
    end: int
    is_function: bool

    @property
    def span(self) -> int:
        return self.end - self.start + 1


def language_for(path: str) -> str | None:
    return _EXTENSIONS.get(PurePosixPath(path).suffix.lower())


@lru_cache(maxsize=None)
def _parser(language: str) -> Parser:
    module, factory = _GRAMMARS[language]
    return Parser(Language(getattr(importlib.import_module(module), factory)()))


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace")


def _name_of(node: Node) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named)
    if node.type == "impl_item":
        target = node.child_by_field_name("type")
        return _text(target) if target is not None else None
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type in _NAME_NODES:
            return _text(declarator)
        declarator = declarator.child_by_field_name("declarator")
    return None


def index_symbols(source: bytes, language: str) -> list[Symbol]:
    functions = _FUNCTION_NODES[language]
    types = _TYPE_NODES[language]
    symbols: list[Symbol] = []
    stack: list[tuple[Node, str]] = [(_parser(language).parse(source).root_node, "")]
    while stack:
        node, scope = stack.pop()
        is_function = node.type in functions
        is_symbol = is_function or node.type in types
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            is_function = is_symbol = value is not None and value.type in _FUNCTION_VALUES
        if node.type in _C_TYPES and node.child_by_field_name("body") is None:
            is_symbol = False  # a reference such as `struct foo *p`, not a definition
        name = _name_of(node) if is_symbol else None
        if name:
            qualified = f"{scope}.{name}" if scope else name
            decorated = node.parent is not None and node.parent.type == "decorated_definition"
            first = node.parent if decorated else node
            symbols.append(Symbol(qualified, first.start_point[0] + 1, node.end_point[0] + 1, is_function))
            scope = qualified
        stack.extend((child, scope) for child in node.children)
    return symbols


def enclosing(symbols: list[Symbol], start: int, end: int) -> Symbol | None:
    """The smallest symbol that contains the whole line range."""
    containing = [s for s in symbols if s.start <= start and end <= s.end]
    return min(containing, key=lambda s: s.span, default=None)
