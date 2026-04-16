"""search_ast — cross-language AST pattern matching with enrichment.

A universal pattern engine that maps one query to language-specific
tree-sitter node types, enabling cross-language anti-pattern detection.
Results are enriched with symbol context, complexity, and test
reachability from the jCodeMunch index.

Supports two modes:
  1. **Preset patterns** — curated anti-pattern detectors that work across
     all supported languages (e.g. ``empty_catch``, ``deeply_nested``).
  2. **Custom patterns** — a mini-DSL for ad-hoc structural queries
     (e.g. ``call:*.unwrap``, ``string:/password/i``).

Every match is attributed to its enclosing indexed symbol, so results
include complexity scores, test reachability, and blast-radius hints.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..storage import IndexStore
from ..parser.languages import LANGUAGE_EXTENSIONS, LANGUAGE_REGISTRY
from ._utils import resolve_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Universal node-type mapping — one concept, every grammar
# ---------------------------------------------------------------------------

_CATCH_NODES: dict[str, list[str]] = {
    "python": ["except_clause"],
    "javascript": ["catch_clause"],
    "typescript": ["catch_clause"],
    "tsx": ["catch_clause"],
    "java": ["catch_clause"],
    "csharp": ["catch_clause"],
    "ruby": ["rescue"],
    "php": ["catch_clause"],
    "cpp": ["catch_clause"],
    "c": [],  # C has no try/catch
    "go": [],  # Go has no try/catch
    "rust": [],  # Rust uses Result, not exceptions
    "kotlin": ["catch_clause"],
    "swift": ["catch_clause"],
    "dart": ["catch_clause"],
}

_CALL_NODES: dict[str, list[str]] = {
    "python": ["call"],
    "javascript": ["call_expression", "new_expression"],
    "typescript": ["call_expression", "new_expression"],
    "tsx": ["call_expression", "new_expression"],
    "go": ["call_expression"],
    "rust": ["call_expression", "macro_invocation"],
    "java": ["method_invocation", "object_creation_expression"],
    "csharp": ["invocation_expression", "object_creation_expression"],
    "ruby": ["call", "method_call"],
    "php": ["function_call_expression", "member_call_expression", "scoped_call_expression"],
    "cpp": ["call_expression"],
    "c": ["call_expression"],
    "kotlin": ["call_expression"],
    "swift": ["call_expression"],
    "dart": ["function_expression_invocation"],
}

_LOOP_NODES: dict[str, list[str]] = {
    "python": ["for_statement", "while_statement"],
    "javascript": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
    "typescript": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
    "tsx": ["for_statement", "for_in_statement", "while_statement", "do_statement"],
    "go": ["for_statement"],
    "rust": ["for_expression", "while_expression", "loop_expression"],
    "java": ["for_statement", "enhanced_for_statement", "while_statement", "do_statement"],
    "csharp": ["for_statement", "for_each_statement", "while_statement", "do_statement"],
    "ruby": ["for", "while", "until"],
    "php": ["for_statement", "foreach_statement", "while_statement", "do_statement"],
    "cpp": ["for_statement", "while_statement", "do_statement", "for_range_loop"],
    "c": ["for_statement", "while_statement", "do_statement"],
    "kotlin": ["for_statement", "while_statement", "do_while_statement"],
    "swift": ["for_statement", "while_statement", "repeat_while_statement"],
    "dart": ["for_statement", "while_statement", "do_statement"],
}

_CONDITION_NODES: dict[str, list[str]] = {
    "python": ["if_statement", "elif_clause"],
    "javascript": ["if_statement", "switch_statement", "ternary_expression"],
    "typescript": ["if_statement", "switch_statement", "ternary_expression"],
    "tsx": ["if_statement", "switch_statement", "ternary_expression"],
    "go": ["if_statement", "expression_switch_statement", "type_switch_statement"],
    "rust": ["if_expression", "match_expression"],
    "java": ["if_statement", "switch_expression"],
    "csharp": ["if_statement", "switch_statement", "switch_expression"],
    "ruby": ["if", "unless", "case"],
    "php": ["if_statement", "switch_statement", "match_expression"],
    "cpp": ["if_statement", "switch_statement"],
    "c": ["if_statement", "switch_statement"],
    "kotlin": ["if_expression", "when_expression"],
    "swift": ["if_statement", "switch_statement", "guard_statement"],
    "dart": ["if_statement", "switch_statement"],
}

_BLOCK_NODES: dict[str, list[str]] = {
    "python": ["block"],
    "javascript": ["statement_block"],
    "typescript": ["statement_block"],
    "tsx": ["statement_block"],
    "go": ["block"],
    "rust": ["block"],
    "java": ["block"],
    "csharp": ["block"],
    "ruby": ["body_statement", "do_block", "block"],
    "php": ["compound_statement"],
    "cpp": ["compound_statement"],
    "c": ["compound_statement"],
    "kotlin": ["function_body"],
    "swift": ["code_block"],
    "dart": ["block"],
}

_FUNCTION_NODES: dict[str, list[str]] = {
    "python": ["function_definition"],
    "javascript": ["function_declaration", "arrow_function", "method_definition", "function"],
    "typescript": ["function_declaration", "arrow_function", "method_definition", "function"],
    "tsx": ["function_declaration", "arrow_function", "method_definition", "function"],
    "go": ["function_declaration", "method_declaration"],
    "rust": ["function_item"],
    "java": ["method_declaration", "constructor_declaration"],
    "csharp": ["method_declaration", "constructor_declaration", "local_function_statement"],
    "ruby": ["method", "singleton_method"],
    "php": ["function_definition", "method_declaration"],
    "cpp": ["function_definition"],
    "c": ["function_definition"],
    "kotlin": ["function_declaration"],
    "swift": ["function_declaration"],
    "dart": ["function_signature", "method_signature"],
}

# Nesting-inducing node types (loops + conditions + catch + functions)
_NESTING_NODES: dict[str, set[str]] = {}
for _lang in set(
    list(_LOOP_NODES) + list(_CONDITION_NODES)
    + list(_CATCH_NODES) + list(_BLOCK_NODES)
):
    _s: set[str] = set()
    for _m in (_LOOP_NODES, _CONDITION_NODES, _CATCH_NODES):
        _s.update(_m.get(_lang, []))
    _NESTING_NODES[_lang] = _s

# Comment node types — virtually universal
_COMMENT_NODES: set[str] = {
    "comment", "line_comment", "block_comment",
    "documentation_comment", "doc_comment",
}


# ---------------------------------------------------------------------------
# Preset pattern catalog
# ---------------------------------------------------------------------------

class _Severity:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_PRESET_CATALOG: dict[str, dict[str, Any]] = {
    "empty_catch": {
        "description": "Exception handler with an empty or pass-only body — silently swallows errors",
        "severity": _Severity.ERROR,
        "category": "error_handling",
    },
    "bare_except": {
        "description": "Catch-all exception handler without a specific type — masks unrelated errors",
        "severity": _Severity.WARNING,
        "category": "error_handling",
    },
    "deeply_nested": {
        "description": "Code nested 5+ control-flow levels deep — hard to reason about",
        "severity": _Severity.WARNING,
        "category": "complexity",
        "threshold": 5,
    },
    "god_function": {
        "description": "Function exceeding 100 lines or cyclomatic complexity > 15",
        "severity": _Severity.WARNING,
        "category": "complexity",
    },
    "todo_fixme": {
        "description": "TODO / FIXME / HACK / XXX marker in comments — unfinished work",
        "severity": _Severity.INFO,
        "category": "maintenance",
    },
    "eval_exec": {
        "description": "Dynamic code execution (eval, exec, Function constructor, vm.runInNewContext) — injection risk",
        "severity": _Severity.ERROR,
        "category": "security",
    },
    "hardcoded_secret": {
        "description": "String literal matching credential patterns (password=, api_key=, token=) — leaked secrets",
        "severity": _Severity.ERROR,
        "category": "security",
    },
    "nested_loops": {
        "description": "Triple-nested loop (O(n³) or worse) — potential performance bottleneck",
        "severity": _Severity.WARNING,
        "category": "performance",
        "threshold": 3,
    },
    "magic_number": {
        "description": "Numeric literal outside {-1, 0, 1, 2} in non-constant context — unexplained constant",
        "severity": _Severity.INFO,
        "category": "maintenance",
    },
    "reassigned_param": {
        "description": "Function parameter overwritten in the function body — confusing control flow",
        "severity": _Severity.INFO,
        "category": "maintenance",
    },
}

_CATEGORIES: dict[str, list[str]] = {}
for _p_name, _p_meta in _PRESET_CATALOG.items():
    _CATEGORIES.setdefault(_p_meta["category"], []).append(_p_name)


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

def _node_text(node, source_bytes: bytes) -> str:
    """Decode a tree-sitter node's text."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _child_statement_count(node) -> int:
    """Count non-trivial child statements in a block-like node."""
    trivial = {"comment", "line_comment", "block_comment", "pass_statement", ";", "empty_statement"}
    count = 0
    for child in node.children:
        if child.type not in trivial and child.type not in ("{", "}", "(", ")", ":", "pass"):
            # Python pass
            text = child.text
            if text and text.strip() not in (b"", b"pass", b";"):
                count += 1
    return count


def _detect_empty_catch(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Detect catch/except blocks with empty or trivial bodies."""
    catch_types = set(_CATCH_NODES.get(lang, []))
    if not catch_types:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in catch_types:
            # Find the body child
            body = None
            for child in current.children:
                if child.type in ("block", "statement_block", "compound_statement",
                                  "body_statement", "do_block", "function_body",
                                  "code_block"):
                    body = child
                    break
            # For Python: the body is a direct "block" child
            # If no explicit block found, check if the catch itself has few children
            if body is not None:
                if _child_statement_count(body) == 0:
                    matches.append({
                        "line": current.start_point[0] + 1,
                        "end_line": current.end_point[0] + 1,
                        "column": current.start_point[1],
                        "snippet": _node_text(current, source_bytes)[:200],
                    })
            elif lang == "python":
                # Python except_clause: children are 'except', optional type, ':', block
                block_children = [c for c in current.children if c.type == "block"]
                for blk in block_children:
                    if _child_statement_count(blk) == 0:
                        matches.append({
                            "line": current.start_point[0] + 1,
                            "end_line": current.end_point[0] + 1,
                            "column": current.start_point[1],
                            "snippet": _node_text(current, source_bytes)[:200],
                        })
        stack.extend(reversed(current.children))
    return matches


def _detect_bare_except(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Detect catch-all handlers without a specific exception type."""
    matches = []
    catch_types = set(_CATCH_NODES.get(lang, []))
    if not catch_types:
        return []

    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in catch_types:
            is_bare = False
            if lang == "python":
                # Python except_clause: bare if no type child after 'except'
                has_type = any(
                    c.type not in ("except", ":", "block", "as", "identifier", "comment")
                    or (c.type == "identifier" and c.prev_sibling and c.prev_sibling.type == "as")
                    for c in current.children
                    if c.type not in ("except", ":", "block", "comment", "as")
                    and not (c.type == "identifier" and c.prev_sibling and
                             c.prev_sibling.type == "as")
                )
                # Simpler: bare except has no type between 'except' and ':'
                child_types = [c.type for c in current.children]
                if "except" in child_types:
                    exc_idx = child_types.index("except")
                    # Look for a type expression between except and :
                    type_found = False
                    for c in current.children[exc_idx + 1:]:
                        if c.type == ":":
                            break
                        if c.type == "as":
                            break
                        if c.type not in ("comment", "line_comment"):
                            type_found = True
                            break
                    if not type_found:
                        is_bare = True
            else:
                # JS/Java/C#/etc: catch_clause without a formal_parameter / catch_declaration
                has_param = any(
                    c.type in ("catch_formal_parameter", "formal_parameter",
                               "catch_declaration", "catch_type",
                               "formal_parameters", "identifier")
                    for c in current.children
                )
                if not has_param:
                    is_bare = True

            if is_bare:
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": _node_text(current, source_bytes)[:200],
                })
        stack.extend(reversed(current.children))
    return matches


def _detect_deeply_nested(node, lang: str, source_bytes: bytes,
                          threshold: int = 5) -> list[dict]:
    """Find code points nested beyond *threshold* control-flow levels."""
    nesting_types = _NESTING_NODES.get(lang, set())
    if not nesting_types:
        return []

    matches = []
    # Stack: (node, current_depth)
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        is_nesting = current.type in nesting_types
        new_depth = depth + 1 if is_nesting else depth
        if is_nesting and new_depth >= threshold:
            matches.append({
                "line": current.start_point[0] + 1,
                "end_line": current.end_point[0] + 1,
                "column": current.start_point[1],
                "snippet": _node_text(current, source_bytes)[:150],
                "nesting_depth": new_depth,
            })
        else:
            for child in reversed(current.children):
                stack.append((child, new_depth))
    return matches


def _detect_nested_loops(node, lang: str, source_bytes: bytes,
                         threshold: int = 3) -> list[dict]:
    """Detect loops nested *threshold* levels deep (O(n^threshold))."""
    loop_types = set(_LOOP_NODES.get(lang, []))
    if not loop_types:
        return []

    matches = []
    # Stack: (node, loop_depth)
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        current, loop_depth = stack.pop()
        is_loop = current.type in loop_types
        new_depth = loop_depth + 1 if is_loop else loop_depth
        if is_loop and new_depth >= threshold:
            matches.append({
                "line": current.start_point[0] + 1,
                "end_line": current.end_point[0] + 1,
                "column": current.start_point[1],
                "snippet": _node_text(current, source_bytes)[:150],
                "loop_depth": new_depth,
            })
        else:
            for child in reversed(current.children):
                stack.append((child, new_depth))
    return matches


_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|NOCOMMIT|TEMP)\b", re.IGNORECASE)


def _detect_todo_fixme(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find TODO/FIXME/HACK/XXX markers in comments."""
    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _COMMENT_NODES:
            text = _node_text(current, source_bytes)
            m = _TODO_RE.search(text)
            if m:
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": text.strip()[:200],
                    "marker": m.group(1).upper(),
                })
        stack.extend(reversed(current.children))
    return matches


# Dangerous dynamic-execution functions per language
_EVAL_NAMES: dict[str, set[str]] = {
    "python": {"eval", "exec", "compile", "__import__"},
    "javascript": {"eval", "Function", "setTimeout", "setInterval"},
    "typescript": {"eval", "Function", "setTimeout", "setInterval"},
    "tsx": {"eval", "Function", "setTimeout", "setInterval"},
    "ruby": {"eval", "instance_eval", "class_eval", "module_eval", "send", "public_send"},
    "php": {"eval", "assert", "preg_replace", "create_function", "call_user_func"},
    "go": {},  # Go has no eval
    "rust": {},  # Rust has no eval
    "java": {},  # Java reflection is different
    "csharp": {},
    "cpp": {"system"},
    "c": {"system"},
}


def _detect_eval_exec(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find dynamic code execution calls."""
    call_types = set(_CALL_NODES.get(lang, []))
    dangerous = _EVAL_NAMES.get(lang, set())
    if not call_types or not dangerous:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in call_types:
            # Extract the callee name
            name = _extract_simple_call_name(current, source_bytes)
            if name and name in dangerous:
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": _node_text(current, source_bytes)[:200],
                    "callee": name,
                })
        stack.extend(reversed(current.children))
    return matches


_SECRET_RE = re.compile(
    r"""(?:password|passwd|secret|api_key|apikey|token|auth_token|access_key"""
    r"""|private_key|credentials?)\s*[=:]\s*['"][^'"]{4,}['"]""",
    re.IGNORECASE,
)


def _detect_hardcoded_secret(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find string literals that look like hardcoded credentials."""
    matches = []
    string_types = {"string", "string_literal", "template_string",
                    "interpreted_string_literal", "raw_string_literal",
                    "string_content", "concatenated_string"}
    # Walk assignment-like nodes looking for secret patterns
    stack = [node]
    while stack:
        current = stack.pop()
        # Check larger expressions for the pattern (assignment context)
        if current.type in ("assignment", "variable_declaration", "variable_declarator",
                            "short_var_declaration", "pair", "keyword_argument",
                            "named_argument", "assignment_expression",
                            "lexical_declaration", "expression_statement"):
            text = _node_text(current, source_bytes)
            if _SECRET_RE.search(text):
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": text[:200],
                })
                continue  # Don't recurse into children
        stack.extend(reversed(current.children))
    return matches


_MAGIC_NUMBER_RE = re.compile(r"^-?\d+\.?\d*$")
_SAFE_NUMBERS = frozenset({"-1", "-1.0", "0", "0.0", "1", "1.0", "2", "2.0",
                            "100", "1000", "0.5", "0.0", "1.0", "255", "256",
                            "1024", "0x00", "0xff", "0xFF"})


def _detect_magic_number(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find numeric literals outside common safe values in non-constant context."""
    number_types = {"integer", "float", "number", "integer_literal",
                    "float_literal", "decimal_integer_literal",
                    "decimal_floating_point_literal", "number_literal"}
    # Constant declaration node types (skip these)
    const_parents = {"const_declaration", "const_spec", "const_item",
                     "constant_declaration", "enum_variant",
                     "enum_assignment", "global_statement"}

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in number_types:
            text = _node_text(current, source_bytes).strip()
            if text not in _SAFE_NUMBERS and _MAGIC_NUMBER_RE.match(text):
                # Check if in constant context
                parent = current.parent
                in_const = False
                depth = 0
                while parent and depth < 5:
                    if parent.type in const_parents:
                        in_const = True
                        break
                    depth += 1
                    parent = parent.parent
                if not in_const:
                    matches.append({
                        "line": current.start_point[0] + 1,
                        "end_line": current.end_point[0] + 1,
                        "column": current.start_point[1],
                        "snippet": text,
                        "value": text,
                    })
        stack.extend(reversed(current.children))
    return matches


def _detect_god_function(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find functions exceeding 100 lines or with very high branch density."""
    func_types = set(_FUNCTION_NODES.get(lang, []))
    if not func_types:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in func_types:
            start_line = current.start_point[0] + 1
            end_line = current.end_point[0] + 1
            lines = end_line - start_line + 1
            if lines > 100:
                # Extract name
                name = _extract_func_name(current) or "(anonymous)"
                matches.append({
                    "line": start_line,
                    "end_line": end_line,
                    "column": current.start_point[1],
                    "snippet": f"{name} ({lines} lines)",
                    "function_name": name,
                    "line_count": lines,
                })
            # Don't recurse into nested functions for this detector
            continue
        stack.extend(reversed(current.children))
    return matches


def _detect_reassigned_param(node, lang: str, source_bytes: bytes) -> list[dict]:
    """Find function parameters that are reassigned in the function body."""
    func_types = set(_FUNCTION_NODES.get(lang, []))
    if not func_types:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in func_types:
            # Collect parameter names
            param_names = _extract_param_names(current, lang, source_bytes)
            if param_names:
                # Scan the function body for assignments to those names
                body_matches = _find_param_reassignments(
                    current, param_names, lang, source_bytes
                )
                matches.extend(body_matches)
            continue  # Don't double-recurse
        stack.extend(reversed(current.children))
    return matches


def _extract_param_names(func_node, lang: str, source_bytes: bytes) -> set[str]:
    """Extract parameter names from a function node."""
    names: set[str] = set()
    param_node_types = {"parameters", "formal_parameters", "parameter_list",
                        "function_parameters", "lambda_parameters",
                        "formal_parameter_list", "method_parameters"}
    for child in func_node.children:
        if child.type in param_node_types:
            _collect_identifiers_in_params(child, names, source_bytes)
            break
    return names


def _collect_identifiers_in_params(node, names: set[str], source_bytes: bytes) -> None:
    """Recursively collect parameter identifier names."""
    param_id_types = {"identifier", "simple_identifier", "name", "variable_name"}
    # Skip type annotations and defaults
    skip_types = {"type", "type_annotation", "default_value", "type_identifier",
                  "predefined_type", "generic_type"}
    if node.type in skip_types:
        return
    if node.type in param_id_types and node.parent and node.parent.type not in skip_types:
        text = _node_text(node, source_bytes)
        if text and text != "self" and text != "cls" and text != "this":
            names.add(text)
    for child in node.children:
        _collect_identifiers_in_params(child, names, source_bytes)


def _find_param_reassignments(func_node, param_names: set[str],
                              lang: str, source_bytes: bytes) -> list[dict]:
    """Find assignments to parameter names within a function body."""
    assign_types = {"assignment", "augmented_assignment", "assignment_expression",
                    "update_expression"}
    results = []
    # Find the body
    body = None
    for child in func_node.children:
        if child.type in ("block", "statement_block", "compound_statement",
                          "function_body", "code_block", "body_statement"):
            body = child
            break
    if body is None:
        return results

    stack = [body]
    seen_lines: set[int] = set()
    while stack:
        current = stack.pop()
        if current.type in assign_types:
            # Get the left-hand side
            if current.children:
                lhs = current.children[0]
                lhs_text = _node_text(lhs, source_bytes).strip()
                if lhs_text in param_names:
                    line = current.start_point[0] + 1
                    if line not in seen_lines:
                        seen_lines.add(line)
                        results.append({
                            "line": line,
                            "end_line": current.end_point[0] + 1,
                            "column": current.start_point[1],
                            "snippet": _node_text(current, source_bytes)[:200],
                            "parameter": lhs_text,
                        })
        # Don't recurse into nested functions
        if current.type not in set(_FUNCTION_NODES.get(lang, [])):
            stack.extend(reversed(current.children))
    return results


# ---------------------------------------------------------------------------
# Custom pattern mini-DSL
# ---------------------------------------------------------------------------

def _extract_simple_call_name(node, source_bytes: bytes) -> Optional[str]:
    """Extract the callee name from a call node (simple or member)."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier",
                          "field_identifier", "simple_identifier"):
            return _node_text(child, source_bytes)
        if child.type in ("member_expression", "attribute", "field_expression",
                          "scoped_identifier"):
            # Get the last identifier
            ids = [c for c in child.children
                   if c.type in ("identifier", "property_identifier",
                                 "field_identifier", "simple_identifier")]
            if ids:
                return _node_text(ids[-1], source_bytes)
        if child.type in ("(", "argument_list", "arguments"):
            break
    return None


def _extract_func_name(func_node) -> Optional[str]:
    """Extract the function name from a function node."""
    for child in func_node.children:
        if child.type in ("identifier", "property_identifier",
                          "field_identifier", "simple_identifier", "name"):
            return child.text.decode("utf-8", errors="replace") if child.text else None
    return None


def _parse_custom_pattern(pattern: str) -> Optional[dict]:
    """Parse a custom pattern string into a detector config.

    Supported patterns:
        call:NAME        — match function/method calls (glob: *.unwrap, eval, re.*)
        string:/REGEX/i  — match string literals by regex
        comment:/REGEX/i — match comments by regex
        nesting:N+       — match code nested N+ levels
        loops:N+         — match N-deep nested loops
        lines:N+         — match functions over N lines
    """
    if ":" not in pattern:
        return None

    kind, _, value = pattern.partition(":")
    kind = kind.strip().lower()
    value = value.strip()

    if kind == "call":
        return {"type": "call", "name_glob": value}
    elif kind == "string":
        regex, flags = _parse_regex_value(value)
        if regex:
            return {"type": "string", "regex": regex, "flags": flags}
    elif kind == "comment":
        regex, flags = _parse_regex_value(value)
        if regex:
            return {"type": "comment", "regex": regex, "flags": flags}
    elif kind == "nesting":
        n = _parse_threshold(value)
        if n:
            return {"type": "nesting", "threshold": n}
    elif kind == "loops":
        n = _parse_threshold(value)
        if n:
            return {"type": "loops", "threshold": n}
    elif kind == "lines":
        n = _parse_threshold(value)
        if n:
            return {"type": "lines", "threshold": n}
    return None


def _parse_regex_value(value: str) -> tuple[Optional[str], int]:
    """Parse /REGEX/flags into (pattern, re_flags)."""
    if value.startswith("/"):
        # /pattern/flags
        last_slash = value.rfind("/")
        if last_slash > 0:
            pattern = value[1:last_slash]
            flag_str = value[last_slash + 1:]
            flags = 0
            if "i" in flag_str:
                flags |= re.IGNORECASE
            return pattern, flags
    # Bare string — treat as literal
    return re.escape(value), 0


def _parse_threshold(value: str) -> Optional[int]:
    """Parse '5+' or '5' into an integer."""
    value = value.rstrip("+").strip()
    try:
        return int(value)
    except ValueError:
        return None


def _run_custom_pattern(parsed: dict, root_node, lang: str,
                        source_bytes: bytes) -> list[dict]:
    """Execute a parsed custom pattern against an AST."""
    ptype = parsed["type"]

    if ptype == "call":
        return _custom_call_search(root_node, lang, source_bytes, parsed["name_glob"])
    elif ptype == "string":
        return _custom_string_search(root_node, source_bytes,
                                     parsed["regex"], parsed["flags"])
    elif ptype == "comment":
        return _custom_comment_search(root_node, source_bytes,
                                      parsed["regex"], parsed["flags"])
    elif ptype == "nesting":
        return _detect_deeply_nested(root_node, lang, source_bytes, parsed["threshold"])
    elif ptype == "loops":
        return _detect_nested_loops(root_node, lang, source_bytes, parsed["threshold"])
    elif ptype == "lines":
        return _custom_long_functions(root_node, lang, source_bytes, parsed["threshold"])
    return []


def _custom_call_search(node, lang: str, source_bytes: bytes,
                        name_glob: str) -> list[dict]:
    """Find call nodes whose callee matches a glob pattern."""
    call_types = set(_CALL_NODES.get(lang, []))
    if not call_types:
        return []

    # Also support dotted patterns like "os.system" by matching full call text
    dot_pattern = "." in name_glob

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in call_types:
            if dot_pattern:
                # Match against full callee expression text
                callee_text = ""
                for child in current.children:
                    if child.type in ("(", "argument_list", "arguments"):
                        break
                    callee_text = _node_text(child, source_bytes)
                if callee_text and fnmatch.fnmatch(callee_text, name_glob):
                    matches.append({
                        "line": current.start_point[0] + 1,
                        "end_line": current.end_point[0] + 1,
                        "column": current.start_point[1],
                        "snippet": _node_text(current, source_bytes)[:200],
                        "callee": callee_text,
                    })
            else:
                name = _extract_simple_call_name(current, source_bytes)
                if name and fnmatch.fnmatch(name, name_glob):
                    matches.append({
                        "line": current.start_point[0] + 1,
                        "end_line": current.end_point[0] + 1,
                        "column": current.start_point[1],
                        "snippet": _node_text(current, source_bytes)[:200],
                        "callee": name,
                    })
        stack.extend(reversed(current.children))
    return matches


def _custom_string_search(node, source_bytes: bytes,
                          regex: str, flags: int) -> list[dict]:
    """Find string literal nodes matching a regex."""
    string_types = {"string", "string_literal", "template_string",
                    "interpreted_string_literal", "raw_string_literal",
                    "encapsed_string", "heredoc_body", "string_content",
                    "concatenated_string"}
    try:
        pat = re.compile(regex, flags)
    except re.error:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in string_types:
            text = _node_text(current, source_bytes)
            if pat.search(text):
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": text[:200],
                })
            continue  # Don't recurse into string children
        stack.extend(reversed(current.children))
    return matches


def _custom_comment_search(node, source_bytes: bytes,
                           regex: str, flags: int) -> list[dict]:
    """Find comment nodes matching a regex."""
    try:
        pat = re.compile(regex, flags)
    except re.error:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _COMMENT_NODES:
            text = _node_text(current, source_bytes)
            if pat.search(text):
                matches.append({
                    "line": current.start_point[0] + 1,
                    "end_line": current.end_point[0] + 1,
                    "column": current.start_point[1],
                    "snippet": text.strip()[:200],
                })
        stack.extend(reversed(current.children))
    return matches


def _custom_long_functions(node, lang: str, source_bytes: bytes,
                           threshold: int) -> list[dict]:
    """Find functions exceeding *threshold* lines."""
    func_types = set(_FUNCTION_NODES.get(lang, []))
    if not func_types:
        return []

    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in func_types:
            start_line = current.start_point[0] + 1
            end_line = current.end_point[0] + 1
            lines = end_line - start_line + 1
            if lines > threshold:
                name = _extract_func_name(current) or "(anonymous)"
                matches.append({
                    "line": start_line,
                    "end_line": end_line,
                    "column": current.start_point[1],
                    "snippet": f"{name} ({lines} lines)",
                    "function_name": name,
                    "line_count": lines,
                })
            continue
        stack.extend(reversed(current.children))
    return matches


# ---------------------------------------------------------------------------
# Enrichment — attribute matches to indexed symbols
# ---------------------------------------------------------------------------

def _enrich_matches(
    matches: list[dict],
    file_path: str,
    index,
) -> None:
    """Add symbol context, complexity, and test reachability to each match."""
    if not index or not matches:
        return

    # Build a sorted list of symbols in this file for bisect lookup
    file_syms = sorted(
        (
            (s.get("line", 0), s.get("end_line", 0), s)
            for s in index.symbols
            if s.get("file") == file_path
        ),
        key=lambda t: t[0],
    )
    if not file_syms:
        return

    for match in matches:
        line = match.get("line", 0)
        # Find enclosing symbol by line range
        best = None
        for start_line, end_line, sym in file_syms:
            if start_line <= line <= (end_line or start_line + 999):
                if best is None or start_line >= best[0]:
                    best = (start_line, end_line, sym)
        if best:
            sym = best[2]
            match["enclosing_symbol"] = sym.get("id", sym.get("name", ""))
            match["symbol_kind"] = sym.get("kind", "")
            cyc = sym.get("cyclomatic", 0)
            if cyc:
                match["symbol_complexity"] = cyc


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

_PRESET_DETECTORS: dict[str, Any] = {
    "empty_catch": _detect_empty_catch,
    "bare_except": _detect_bare_except,
    "deeply_nested": _detect_deeply_nested,
    "nested_loops": _detect_nested_loops,
    "god_function": _detect_god_function,
    "todo_fixme": _detect_todo_fixme,
    "eval_exec": _detect_eval_exec,
    "hardcoded_secret": _detect_hardcoded_secret,
    "magic_number": _detect_magic_number,
    "reassigned_param": _detect_reassigned_param,
}


def search_ast(
    repo: str,
    pattern: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[str] = None,
    file_pattern: Optional[str] = None,
    max_results: int = 50,
    storage_path: Optional[str] = None,
) -> dict:
    """Cross-language AST pattern matching with symbol enrichment.

    Modes (exactly one of *pattern* or *category* must be provided):
        - **pattern**: a preset name (``empty_catch``, ``eval_exec``, …) or a
          custom mini-DSL query (``call:*.unwrap``, ``string:/password/i``,
          ``nesting:5+``, ``loops:3+``, ``lines:80+``, ``comment:/TODO/i``).
        - **category**: run *all* presets in a category at once.
          Categories: ``security``, ``error_handling``, ``complexity``,
          ``performance``, ``maintenance``, ``all``.

    Each match is attributed to its enclosing indexed symbol with complexity
    and kind metadata.

    Args:
        repo:         Repository identifier (owner/repo or bare name).
        pattern:      Preset name or custom pattern (see above).
        category:     Run all presets in a category. Mutually exclusive with pattern.
        language:     Restrict scan to one language (e.g. "python", "typescript").
        file_pattern: Glob filter on file paths (e.g. "src/**/*.py").
        max_results:  Cap on total matches returned (default 50).
        storage_path: Optional index storage path override.

    Returns:
        ``{repo, pattern/category, matches_by_file, total_matches,
           severity_counts, _meta}``
    """
    t0 = time.perf_counter()

    if not pattern and not category:
        return {"error": "Provide either 'pattern' or 'category'. "
                "Presets: " + ", ".join(sorted(_PRESET_CATALOG)) + ". "
                "Categories: " + ", ".join(sorted(_CATEGORIES)) + ", all. "
                "Custom: call:NAME, string:/REGEX/, comment:/REGEX/, "
                "nesting:N+, loops:N+, lines:N+."}

    # Resolve repo
    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    source_root = getattr(index, "source_root", "") or ""
    if not source_root or not Path(source_root).is_dir():
        return {"error": f"Source root not found on disk ({source_root!r}). "
                "search_ast requires local files — re-index with index_folder."}

    # Determine which detectors to run
    detectors: list[tuple[str, Any, dict]] = []  # (name, func, preset_meta_or_empty)

    if category:
        cat_lower = category.lower()
        if cat_lower == "all":
            preset_names = list(_PRESET_CATALOG)
        elif cat_lower in _CATEGORIES:
            preset_names = _CATEGORIES[cat_lower]
        else:
            return {"error": f"Unknown category {category!r}. "
                    f"Available: {', '.join(sorted(_CATEGORIES))}, all."}
        for pname in preset_names:
            detectors.append((pname, _PRESET_DETECTORS[pname], _PRESET_CATALOG[pname]))
    elif pattern in _PRESET_CATALOG:
        detectors.append((pattern, _PRESET_DETECTORS[pattern], _PRESET_CATALOG[pattern]))
    else:
        # Try custom pattern DSL
        parsed = _parse_custom_pattern(pattern)
        if parsed is None:
            # Check for close matches
            close = [p for p in _PRESET_CATALOG if pattern.lower() in p]
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return {"error": f"Unknown pattern {pattern!r}.{hint} "
                    "Presets: " + ", ".join(sorted(_PRESET_CATALOG)) + ". "
                    "Custom syntax: call:NAME, string:/REGEX/, comment:/REGEX/, "
                    "nesting:N+, loops:N+, lines:N+."}
        detectors.append((pattern, None, {}))  # None func = custom

    # Collect files to scan
    file_langs = getattr(index, "file_languages", {}) or {}
    # Fallback: derive language from extensions
    all_files: set[str] = set()
    for sym in index.symbols:
        f = sym.get("file")
        if f:
            all_files.add(f)

    files_to_scan: list[tuple[str, str]] = []  # (relative_path, language)
    for fpath in sorted(all_files):
        lang_name = file_langs.get(fpath, "")
        if not lang_name:
            ext = os.path.splitext(fpath)[1].lower()
            lang_name = LANGUAGE_EXTENSIONS.get(ext, "")
        if not lang_name:
            continue
        if language and lang_name != language.lower():
            continue
        if file_pattern and not fnmatch.fnmatch(fpath, file_pattern):
            continue
        files_to_scan.append((fpath, lang_name))

    # Run detectors across files
    from tree_sitter_language_pack import get_parser

    all_matches: list[dict] = []
    files_scanned = 0
    files_with_matches = 0
    languages_seen: set[str] = set()
    severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}

    for fpath, lang_name in files_to_scan:
        if len(all_matches) >= max_results:
            break

        abs_path = Path(source_root) / fpath
        if not abs_path.is_file():
            continue

        try:
            source_bytes = abs_path.read_bytes()
        except OSError:
            continue

        # Get the tree-sitter language name
        spec = LANGUAGE_REGISTRY.get(lang_name)
        ts_lang = spec.ts_language if spec else lang_name

        try:
            parser = get_parser(ts_lang)
            tree = parser.parse(source_bytes)
        except Exception:
            continue

        files_scanned += 1
        languages_seen.add(lang_name)
        file_had_matches = False

        for det_name, det_func, det_meta in detectors:
            if len(all_matches) >= max_results:
                break

            if det_func is not None:
                # Preset detector
                kwargs: dict[str, Any] = {}
                threshold = det_meta.get("threshold")
                if threshold is not None:
                    kwargs["threshold"] = threshold
                file_matches = det_func(tree.root_node, lang_name, source_bytes, **kwargs)
                severity = det_meta.get("severity", _Severity.INFO)
            else:
                # Custom pattern
                parsed = _parse_custom_pattern(det_name)
                if parsed is None:
                    continue
                file_matches = _run_custom_pattern(parsed, tree.root_node,
                                                   lang_name, source_bytes)
                severity = _Severity.INFO

            for match in file_matches:
                if len(all_matches) >= max_results:
                    break
                match["file"] = fpath
                match["language"] = lang_name
                match["pattern"] = det_name
                match["severity"] = severity
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                # Enrich with symbol context
                _enrich_matches([match], fpath, index)
                all_matches.append(match)
                file_had_matches = True

        if file_had_matches:
            files_with_matches += 1

    # Sort: errors first, then warnings, then info; within severity by file+line
    severity_order = {"error": 0, "warning": 1, "info": 2}
    all_matches.sort(key=lambda m: (
        severity_order.get(m.get("severity", "info"), 3),
        m.get("file", ""),
        m.get("line", 0),
    ))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Build result
    result: dict[str, Any] = {
        "repo": f"{owner}/{name}",
        "total_matches": len(all_matches),
        "severity_counts": {k: v for k, v in severity_counts.items() if v > 0},
        "matches": all_matches,
        "truncated": len(all_matches) >= max_results,
        "_meta": {
            "files_scanned": files_scanned,
            "files_with_matches": files_with_matches,
            "languages_scanned": sorted(languages_seen),
            "elapsed_ms": elapsed_ms,
        },
    }

    if category:
        result["category"] = category
        result["patterns_run"] = [d[0] for d in detectors]
    else:
        result["pattern"] = pattern
        if pattern in _PRESET_CATALOG:
            result["description"] = _PRESET_CATALOG[pattern]["description"]

    return result
