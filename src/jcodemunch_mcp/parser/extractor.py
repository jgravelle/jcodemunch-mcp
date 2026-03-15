"""Generic AST symbol extractor using tree-sitter."""

import re
from typing import Optional
from tree_sitter_language_pack import get_parser

from .symbols import Symbol, make_symbol_id, compute_content_hash
from .languages import LanguageSpec, LANGUAGE_REGISTRY


def parse_file(content: str, filename: str, language: str) -> list[Symbol]:
    """Parse source code and extract symbols using tree-sitter.
    
    Args:
        content: Raw source code
        filename: File path (for ID generation)
        language: Language name (must be in LANGUAGE_REGISTRY)
    
    Returns:
        List of Symbol objects
    """
    if language not in LANGUAGE_REGISTRY:
        return []
    
    source_bytes = content.encode("utf-8")

    if language == "cpp":
        symbols = _parse_cpp_symbols(source_bytes, filename)
    elif language == "elixir":
        symbols = _parse_elixir_symbols(source_bytes, filename)
    elif language == "blade":
        symbols = _parse_blade_symbols(source_bytes, filename)
    elif language == "nix":
        symbols = _parse_nix_symbols(source_bytes, filename)
    elif language == "vue":
        symbols = _parse_vue_symbols(source_bytes, filename)
    elif language == "ejs":
        symbols = _parse_ejs_symbols(source_bytes, filename)
    elif language == "verse":
        symbols = _parse_verse_symbols(source_bytes, filename)
    elif language == "lua":
        symbols = _parse_lua_symbols(source_bytes, filename)
    elif language == "luau":
        symbols = _parse_luau_symbols(source_bytes, filename)
    elif language == "erlang":
        symbols = _parse_erlang_symbols(source_bytes, filename)
    elif language == "fortran":
        symbols = _parse_fortran_symbols(source_bytes, filename)
    elif language == "sql":
        symbols = _parse_sql_symbols(source_bytes, filename)
    elif language == "objc":
        symbols = _parse_objc_symbols(source_bytes, filename)
    elif language == "proto":
        symbols = _parse_proto_symbols(source_bytes, filename)
    elif language == "hcl":
        symbols = _parse_hcl_symbols(source_bytes, filename)
    elif language == "graphql":
        symbols = _parse_graphql_symbols(source_bytes, filename)
    elif language == "julia":
        symbols = _parse_julia_symbols(source_bytes, filename)
    elif language == "groovy":
        symbols = _parse_groovy_symbols(source_bytes, filename)
    elif language == "autohotkey":
        symbols = _parse_autohotkey_symbols(source_bytes, filename)
    elif language == "asm":
        symbols = _parse_asm_symbols(source_bytes, filename)
    elif language == "xml":
        symbols = _parse_xml_symbols(source_bytes, filename)
    elif language == "openapi":
        symbols = _parse_openapi_symbols(source_bytes, filename)
    else:
        spec = LANGUAGE_REGISTRY[language]
        symbols = _parse_with_spec(source_bytes, filename, language, spec)

    # Disambiguate overloaded symbols (same ID)
    symbols = _disambiguate_overloads(symbols)

    return symbols


def _parse_with_spec(
    source_bytes: bytes,
    filename: str,
    language: str,
    spec: LanguageSpec,
) -> list[Symbol]:
    """Parse source bytes using one language spec."""
    try:
        parser = get_parser(spec.ts_language)
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    symbols: list[Symbol] = []
    _walk_tree(tree.root_node, spec, source_bytes, filename, language, symbols, None)
    return symbols


def _parse_cpp_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse C++ and auto-fallback to C for `.h` files with no C++ symbols."""
    cpp_spec = LANGUAGE_REGISTRY["cpp"]
    cpp_symbols: list[Symbol] = []
    cpp_error_nodes = 0
    try:
        parser = get_parser(cpp_spec.ts_language)
        tree = parser.parse(source_bytes)
        cpp_error_nodes = _count_error_nodes(tree.root_node)
        _walk_tree(tree.root_node, cpp_spec, source_bytes, filename, "cpp", cpp_symbols, None)
    except Exception:
        cpp_error_nodes = 10**9

    # Non-headers are always C++.
    if not filename.lower().endswith(".h"):
        return cpp_symbols

    # Header auto-detection: parse both C++ and C, prefer better parse quality.
    c_spec = LANGUAGE_REGISTRY.get("c")
    if not c_spec:
        return cpp_symbols

    c_symbols: list[Symbol] = []
    c_error_nodes = 10**9
    try:
        c_parser = get_parser(c_spec.ts_language)
        c_tree = c_parser.parse(source_bytes)
        c_error_nodes = _count_error_nodes(c_tree.root_node)
        _walk_tree(c_tree.root_node, c_spec, source_bytes, filename, "c", c_symbols, None)
    except Exception:
        c_error_nodes = 10**9

    # If only one parser yields symbols, use that parser's symbols.
    if cpp_symbols and not c_symbols:
        return cpp_symbols
    if c_symbols and not cpp_symbols:
        return c_symbols
    if not cpp_symbols and not c_symbols:
        return cpp_symbols

    # Both yielded symbols: choose fewer parse errors first, then richer symbol output.
    if c_error_nodes < cpp_error_nodes:
        return c_symbols
    if cpp_error_nodes < c_error_nodes:
        return cpp_symbols

    # Same error quality: use lexical signal to break ties for `.h`.
    if _looks_like_cpp_header(source_bytes):
        if len(cpp_symbols) >= len(c_symbols):
            return cpp_symbols
    else:
        return c_symbols

    if len(c_symbols) > len(cpp_symbols):
        return c_symbols

    return cpp_symbols


def _walk_tree(
    node,
    spec: LanguageSpec,
    source_bytes: bytes,
    filename: str,
    language: str,
    symbols: list,
    parent_symbol: Optional[Symbol] = None,
    scope_parts: Optional[list[str]] = None,
    class_scope_depth: int = 0,
):
    """Recursively walk the AST and extract symbols."""
    # Dart: function_signature inside method_signature is handled by method_signature
    if node.type == "function_signature" and node.parent and node.parent.type == "method_signature":
        return

    is_cpp = language == "cpp"
    local_scope_parts = scope_parts or []
    next_parent = parent_symbol
    next_class_scope_depth = class_scope_depth

    if is_cpp and node.type == "namespace_definition":
        ns_name = _extract_cpp_namespace_name(node, source_bytes)
        if ns_name:
            local_scope_parts = [*local_scope_parts, ns_name]

    # Check if this node is a symbol
    if node.type in spec.symbol_node_types:
        # C++ declarations include non-function declarations. Filter those out.
        if not (is_cpp and node.type in {"declaration", "field_declaration"} and not _is_cpp_function_declaration(node)):
            symbol = _extract_symbol(
                node,
                spec,
                source_bytes,
                filename,
                language,
                parent_symbol,
                local_scope_parts,
                class_scope_depth,
            )
            if symbol:
                symbols.append(symbol)
                if is_cpp:
                    if _is_cpp_type_container(node):
                        next_parent = symbol
                        next_class_scope_depth = class_scope_depth + 1
                else:
                    next_parent = symbol

    # Check for arrow/function-expression variable assignments in JS/TS
    if node.type == "variable_declarator" and language in ("javascript", "typescript", "tsx"):
        var_func = _extract_variable_function(
            node, spec, source_bytes, filename, language, parent_symbol
        )
        if var_func:
            symbols.append(var_func)

    # Check for constant patterns (top-level assignments with UPPER_CASE names)
    if node.type in spec.constant_patterns and parent_symbol is None:
        const_symbol = _extract_constant(node, spec, source_bytes, filename, language)
        if const_symbol:
            symbols.append(const_symbol)

    # Recurse into children
    for child in node.children:
        _walk_tree(
            child,
            spec,
            source_bytes,
            filename,
            language,
            symbols,
            next_parent,
            local_scope_parts,
            next_class_scope_depth,
        )


def _extract_symbol(
    node,
    spec: LanguageSpec,
    source_bytes: bytes,
    filename: str,
    language: str,
    parent_symbol: Optional[Symbol] = None,
    scope_parts: Optional[list[str]] = None,
    class_scope_depth: int = 0,
) -> Optional[Symbol]:
    """Extract a Symbol from an AST node."""
    kind = spec.symbol_node_types[node.type]
    
    # Skip nodes with errors
    if node.has_error:
        return None
    
    # Extract name
    name = _extract_name(node, spec, source_bytes)
    if not name:
        return None
    
    # Build qualified name
    if language == "cpp":
        if parent_symbol:
            qualified_name = f"{parent_symbol.qualified_name}.{name}"
        elif scope_parts:
            qualified_name = ".".join([*scope_parts, name])
        else:
            qualified_name = name
        if kind == "function" and class_scope_depth > 0:
            kind = "method"
    else:
        if parent_symbol:
            qualified_name = f"{parent_symbol.name}.{name}"
            kind = "method" if kind == "function" else kind
        else:
            qualified_name = name

    signature_node = node
    if language == "cpp":
        wrapper = _nearest_cpp_template_wrapper(node)
        if wrapper:
            signature_node = wrapper

    # Build signature
    signature = _build_signature(signature_node, spec, source_bytes)

    # Extract docstring
    docstring = _extract_docstring(signature_node, spec, source_bytes)

    # Extract decorators
    decorators = _extract_decorators(node, spec, source_bytes)

    start_node = signature_node
    # Dart: function_signature/method_signature have their body as a next sibling
    end_byte = node.end_byte
    end_line_num = node.end_point[0] + 1
    if node.type in ("function_signature", "method_signature"):
        next_sib = node.next_named_sibling
        if next_sib and next_sib.type == "function_body":
            end_byte = next_sib.end_byte
            end_line_num = next_sib.end_point[0] + 1

    # Compute content hash
    symbol_bytes = source_bytes[start_node.start_byte:end_byte]
    c_hash = compute_content_hash(symbol_bytes)

    # Create symbol
    symbol = Symbol(
        id=make_symbol_id(filename, qualified_name, kind),
        file=filename,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        language=language,
        signature=signature,
        docstring=docstring,
        decorators=decorators,
        parent=parent_symbol.id if parent_symbol else None,
        line=start_node.start_point[0] + 1,
        end_line=end_line_num,
        byte_offset=start_node.start_byte,
        byte_length=end_byte - start_node.start_byte,
        content_hash=c_hash,
    )
    
    return symbol


def _extract_name(node, spec: LanguageSpec, source_bytes: bytes) -> Optional[str]:
    """Extract the name from an AST node."""
    # Handle type_declaration in Go - name is in type_spec child
    if node.type == "type_declaration":
        for child in node.children:
            if child.type == "type_spec":
                name_node = child.child_by_field_name("name")
                if name_node:
                    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        return None

    # Dart: mixin_declaration has identifier as direct child (no field name)
    if node.type == "mixin_declaration":
        for child in node.children:
            if child.type == "identifier":
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
        return None

    # Dart: method_signature wraps function_signature or getter_signature
    if node.type == "method_signature":
        for child in node.children:
            if child.type in ("function_signature", "getter_signature"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        return None

    # Dart: type_alias name is the first type_identifier child
    if node.type == "type_alias" and spec.ts_language == "dart":
        for child in node.children:
            if child.type == "type_identifier":
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
        return None

    # Kotlin: no named fields; walk children by type to find name
    if spec.ts_language == "kotlin":
        if node.type in ("class_declaration", "object_declaration", "type_alias"):
            for child in node.children:
                if child.type == "type_identifier":
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
            return None
        if node.type == "function_declaration":
            for child in node.children:
                if child.type == "simple_identifier":
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
            return None

    # Gleam: type_definition and type_alias names live inside a type_name child
    if spec.ts_language == "gleam" and node.type in ("type_definition", "type_alias"):
        for child in node.children:
            if child.type == "type_name":
                name_node = child.child_by_field_name("name")
                if name_node:
                    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        return None

    # C#: field_declaration and event_field_declaration wrappers
    if spec.ts_language == "csharp" and node.type in ("field_declaration", "event_field_declaration"):
        for child in node.children:
            if child.type == "variable_declaration":
                # Find the first variable_declarator child
                for vdecl in child.children:
                    if vdecl.type == "variable_declarator":
                        name_node = vdecl.child_by_field_name("name")
                        if name_node:
                            return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        return None

    if node.type not in spec.name_fields:
        return None
    
    field_name = spec.name_fields[node.type]
    name_node = node.child_by_field_name(field_name)
    
    if name_node:
        if spec.ts_language == "cpp":
            return _extract_cpp_name(name_node, source_bytes)

        # C function_definition: declarator is a function_declarator,
        # which wraps the actual identifier. Unwrap recursively.
        while name_node.type in ("function_declarator", "pointer_declarator", "reference_declarator"):
            inner = name_node.child_by_field_name("declarator")
            if inner:
                name_node = inner
            else:
                break
        return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
    
    return None


def _extract_cpp_name(name_node, source_bytes: bytes) -> Optional[str]:
    """Extract C++ symbol names from nested declarators."""
    current = name_node
    wrapper_types = {
        "function_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "parenthesized_declarator",
        "attributed_declarator",
        "init_declarator",
    }

    while current.type in wrapper_types:
        inner = current.child_by_field_name("declarator")
        if not inner:
            break
        current = inner

    # Prefer typed name children where available.
    if current.type in {"qualified_identifier", "scoped_identifier"}:
        name_node = current.child_by_field_name("name")
        if name_node:
            text = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8").strip()
            if text:
                return text

    subtree_name = _find_cpp_name_in_subtree(current, source_bytes)
    if subtree_name:
        return subtree_name

    text = source_bytes[current.start_byte:current.end_byte].decode("utf-8").strip()
    return text or None


def _find_cpp_name_in_subtree(node, source_bytes: bytes) -> Optional[str]:
    """Best-effort extraction of a callable/type name from a declarator subtree."""
    direct_types = {"identifier", "field_identifier", "operator_name", "destructor_name", "type_identifier"}
    if node.type in direct_types:
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
        return text or None

    if node.type in {"qualified_identifier", "scoped_identifier"}:
        name_node = node.child_by_field_name("name")
        if name_node:
            return _find_cpp_name_in_subtree(name_node, source_bytes)

    for child in node.children:
        if not child.is_named:
            continue
        found = _find_cpp_name_in_subtree(child, source_bytes)
        if found:
            return found
    return None


def _build_signature(node, spec: LanguageSpec, source_bytes: bytes) -> str:
    """Build a clean signature from AST node."""
    if node.type == "template_declaration":
        inner = node.child_by_field_name("declaration")
        if not inner:
            for child in reversed(node.children):
                if child.is_named:
                    inner = child
                    break

        if inner:
            body = inner.child_by_field_name("body")
            end_byte = body.start_byte if body else inner.end_byte
        else:
            end_byte = node.end_byte
    elif spec.ts_language == "csharp" and node.type == "property_declaration":
        # C# properties use 'accessors' field instead of 'body'
        body = node.child_by_field_name("accessors")
        end_byte = body.start_byte if body else node.end_byte
    elif spec.ts_language == "kotlin":
        # Kotlin uses no named fields; find body child by type
        body = None
        for child in node.children:
            if child.type in ("function_body", "class_body", "enum_class_body"):
                body = child
                break
        end_byte = body.start_byte if body else node.end_byte
    else:
        # Find the body child to determine where signature ends
        body = node.child_by_field_name("body")

        if body:
            # Signature is from start of node to start of body
            end_byte = body.start_byte
        else:
            end_byte = node.end_byte
    
    sig_bytes = source_bytes[node.start_byte:end_byte]
    sig_text = sig_bytes.decode("utf-8").strip()
    
    # Clean up: remove trailing '{', ':', etc.
    sig_text = sig_text.rstrip("{: \n\t")
    
    return sig_text


def _nearest_cpp_template_wrapper(node):
    """Return closest enclosing template_declaration (if any)."""
    current = node
    wrapper = None
    while current.parent and current.parent.type == "template_declaration":
        wrapper = current.parent
        current = current.parent
    return wrapper


def _is_cpp_type_container(node) -> bool:
    """C++ node types that can contain methods."""
    return node.type in {"class_specifier", "struct_specifier", "union_specifier"}


def _is_cpp_function_declaration(node) -> bool:
    """True if a C++ declaration node is function-like."""
    if node.type not in {"declaration", "field_declaration"}:
        return True

    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return False
    return _has_function_declarator(declarator)


def _has_function_declarator(node) -> bool:
    """Check subtree for function declarator nodes."""
    if node.type in {"function_declarator", "abstract_function_declarator"}:
        return True

    for child in node.children:
        if child.is_named and _has_function_declarator(child):
            return True
    return False


def _extract_cpp_namespace_name(node, source_bytes: bytes) -> Optional[str]:
    """Extract namespace name from a namespace_definition node."""
    name_node = node.child_by_field_name("name")
    if not name_node:
        for child in node.children:
            if child.type in {"namespace_identifier", "identifier"}:
                name_node = child
                break

    if not name_node:
        return None

    name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8").strip()
    return name or None


def _looks_like_cpp_header(source_bytes: bytes) -> bool:
    """Heuristic: detect obvious C++ constructs in `.h` content."""
    text = source_bytes.decode("utf-8", errors="ignore")
    cpp_markers = (
        "namespace ",
        "class ",
        "template<",
        "template <",
        "constexpr",
        "noexcept",
        "[[",
        "std::",
        "using ",
        "::",
        "public:",
        "private:",
        "protected:",
        "operator",
        "typename",
    )
    return any(marker in text for marker in cpp_markers)


def _count_error_nodes(node) -> int:
    """Count parser ERROR nodes in a syntax tree subtree."""
    count = 1 if node.type == "ERROR" else 0
    for child in node.children:
        count += _count_error_nodes(child)
    return count


def _extract_docstring(node, spec: LanguageSpec, source_bytes: bytes) -> str:
    """Extract docstring using language-specific strategy."""
    if spec.docstring_strategy == "next_sibling_string":
        return _extract_python_docstring(node, source_bytes)
    elif spec.docstring_strategy == "preceding_comment":
        return _extract_preceding_comments(node, source_bytes)
    return ""


def _extract_python_docstring(node, source_bytes: bytes) -> str:
    """Extract Python docstring from first statement in body."""
    body = node.child_by_field_name("body")
    if not body or body.child_count == 0:
        return ""
    
    # Find first expression_statement in body (function docstrings)
    for child in body.children:
        if child.type == "expression_statement":
            # Check if it's a string
            expr = child.child_by_field_name("expression")
            if expr and expr.type == "string":
                doc = source_bytes[expr.start_byte:expr.end_byte].decode("utf-8")
                return _strip_quotes(doc)
            # Handle tree-sitter-python 0.21+ string format
            if child.child_count > 0:
                first = child.children[0]
                if first.type in ("string", "concatenated_string"):
                    doc = source_bytes[first.start_byte:first.end_byte].decode("utf-8")
                    return _strip_quotes(doc)
        # Class docstrings are directly string nodes in the block
        elif child.type == "string":
            doc = source_bytes[child.start_byte:child.end_byte].decode("utf-8")
            return _strip_quotes(doc)
    
    return ""


def _strip_quotes(text: str) -> str:
    """Strip quotes from a docstring."""
    text = text.strip()
    if text.startswith('"""') and text.endswith('"""'):
        return text[3:-3].strip()
    if text.startswith("'''") and text.endswith("'''"):
        return text[3:-3].strip()
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].strip()
    return text


def _extract_preceding_comments(node, source_bytes: bytes) -> str:
    """Extract comments that immediately precede a node."""
    comments = []

    # Walk backwards through siblings, skipping past annotations/decorators
    prev = node.prev_named_sibling
    while prev and prev.type in ("annotation", "marker_annotation"):
        prev = prev.prev_named_sibling
    while prev and prev.type in ("comment", "line_comment", "block_comment", "documentation_comment", "pod"):
        comment_text = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8")
        comments.insert(0, comment_text)
        prev = prev.prev_named_sibling
    
    if not comments:
        return ""
    
    docstring = "\n".join(comments)
    return _clean_comment_markers(docstring)


def _clean_comment_markers(text: str) -> str:
    """Clean comment markers from docstring."""
    # POD block: strip directive lines (=pod, =head1, =cut, etc.), keep content
    if text.lstrip().startswith("="):
        content_lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("="):
                continue
            content_lines.append(stripped)
        return "\n".join(content_lines).strip()

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        # Remove leading comment markers (order matters: longer prefixes first)
        if line.startswith("/**"):
            line = line[3:]
        elif line.startswith("//!"):
            line = line[3:]
        elif line.startswith("///"):
            line = line[3:]
        elif line.startswith("//"):
            line = line[2:]
        elif line.startswith("/*"):
            line = line[2:]
        elif line.startswith("*"):
            line = line[1:]
        elif line.startswith("#"):
            line = line[1:]

        # Remove trailing */
        if line.endswith("*/"):
            line = line[:-2]

        cleaned.append(line.strip())

    return "\n".join(cleaned).strip()


def _extract_decorators(node, spec: LanguageSpec, source_bytes: bytes) -> list[str]:
    """Extract decorators/attributes from a node."""
    if not spec.decorator_node_type:
        return []

    decorators = []

    if spec.decorator_from_children:
        # C#: attribute_list nodes are direct children of the declaration
        for child in node.children:
            if child.type == spec.decorator_node_type:
                decorator_text = source_bytes[child.start_byte:child.end_byte].decode("utf-8")
                decorators.append(decorator_text.strip())
    else:
        # Other languages: decorators are preceding siblings
        prev = node.prev_named_sibling
        while prev and prev.type == spec.decorator_node_type:
            decorator_text = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8")
            decorators.insert(0, decorator_text.strip())
            prev = prev.prev_named_sibling

    return decorators


_VARIABLE_FUNCTION_TYPES = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})


def _extract_variable_function(
    node,
    spec: LanguageSpec,
    source_bytes: bytes,
    filename: str,
    language: str,
    parent_symbol: Optional[Symbol] = None,
) -> Optional[Symbol]:
    """Extract a function from `const name = () => {}` or `const name = function() {}`."""
    # node is a variable_declarator
    name_node = node.child_by_field_name("name")
    if not name_node or name_node.type != "identifier":
        return None  # destructuring or other non-simple binding

    value_node = node.child_by_field_name("value")
    if not value_node or value_node.type not in _VARIABLE_FUNCTION_TYPES:
        return None  # not a function assignment

    name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")

    kind = "function"
    if parent_symbol:
        qualified_name = f"{parent_symbol.name}.{name}"
        kind = "method"
    else:
        qualified_name = name

    # Signature: use the full declaration statement (lexical_declaration parent)
    # to capture export/const keywords
    sig_node = node.parent if node.parent and node.parent.type in (
        "lexical_declaration", "export_statement", "variable_declaration",
    ) else node
    # Walk up through export_statement wrapper if present
    if sig_node.parent and sig_node.parent.type == "export_statement":
        sig_node = sig_node.parent

    signature = _build_signature(sig_node, spec, source_bytes)

    # Docstring: look for preceding comment on the declaration statement
    doc_node = sig_node
    docstring = _extract_docstring(doc_node, spec, source_bytes)

    # Content hash covers the full declaration
    start_byte = sig_node.start_byte
    end_byte = sig_node.end_byte
    symbol_bytes = source_bytes[start_byte:end_byte]
    c_hash = compute_content_hash(symbol_bytes)

    return Symbol(
        id=make_symbol_id(filename, qualified_name, kind),
        file=filename,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        language=language,
        signature=signature,
        docstring=docstring,
        parent=parent_symbol.id if parent_symbol else None,
        line=sig_node.start_point[0] + 1,
        end_line=sig_node.end_point[0] + 1,
        byte_offset=start_byte,
        byte_length=end_byte - start_byte,
        content_hash=c_hash,
    )


def _extract_constant(
    node, spec: LanguageSpec, source_bytes: bytes, filename: str, language: str
) -> Optional[Symbol]:
    """Extract a constant (UPPER_CASE top-level assignment)."""
    # Only extract constants at module level for Python
    if node.type == "assignment":
        left = node.child_by_field_name("left")
        if left and left.type == "identifier":
            name = source_bytes[left.start_byte:left.end_byte].decode("utf-8")
            # Check if UPPER_CASE (constant convention)
            if name.isupper() or (len(name) > 1 and name[0].isupper() and "_" in name):
                # Get the full assignment text as signature
                sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
                const_bytes = source_bytes[node.start_byte:node.end_byte]
                c_hash = compute_content_hash(const_bytes)

                return Symbol(
                    id=make_symbol_id(filename, name, "constant"),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind="constant",
                    language=language,
                    signature=sig[:100],  # Truncate long assignments
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=c_hash,
                )

    # C preprocessor #define macros
    if node.type == "preproc_def":
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            if name.isupper() or (len(name) > 1 and name[0].isupper() and "_" in name):
                sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
                const_bytes = source_bytes[node.start_byte:node.end_byte]
                c_hash = compute_content_hash(const_bytes)

                return Symbol(
                    id=make_symbol_id(filename, name, "constant"),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind="constant",
                    language=language,
                    signature=sig[:100],
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=c_hash,
                )

    # GDScript: const MAX_SPEED: float = 100.0  (all const declarations are constants)
    if node.type == "const_statement":
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
            const_bytes = source_bytes[node.start_byte:node.end_byte]
            c_hash = compute_content_hash(const_bytes)
            return Symbol(
                id=make_symbol_id(filename, name, "constant"),
                file=filename,
                name=name,
                qualified_name=name,
                kind="constant",
                language=language,
                signature=sig[:100],
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                byte_offset=node.start_byte,
                byte_length=node.end_byte - node.start_byte,
                content_hash=c_hash,
            )

    # Perl: use constant NAME => value
    if node.type == "use_statement":
        children = list(node.children)
        if len(children) >= 3 and children[1].type == "package":
            pkg_name = source_bytes[children[1].start_byte:children[1].end_byte].decode("utf-8")
            if pkg_name == "constant":
                for child in children:
                    if child.type == "list_expression" and child.child_count >= 1:
                        name_node = child.children[0]
                        if name_node.type == "autoquoted_bareword":
                            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
                            if name.isupper() or (len(name) > 1 and name[0].isupper()):
                                sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
                                const_bytes = source_bytes[node.start_byte:node.end_byte]
                                c_hash = compute_content_hash(const_bytes)
                                return Symbol(
                                    id=make_symbol_id(filename, name, "constant"),
                                    file=filename,
                                    name=name,
                                    qualified_name=name,
                                    kind="constant",
                                    language=language,
                                    signature=sig[:100],
                                    line=node.start_point[0] + 1,
                                    end_line=node.end_point[0] + 1,
                                    byte_offset=node.start_byte,
                                    byte_length=node.end_byte - node.start_byte,
                                    content_hash=c_hash,
                                )

    # Swift: let MAX_SPEED = 100  (property_declaration with let binding)
    if node.type == "property_declaration":
        # Only extract immutable `let` bindings (not `var`)
        binding = None
        for child in node.children:
            if child.type == "value_binding_pattern":
                binding = child
                break
        if not binding:
            return None
        mutability = binding.child_by_field_name("mutability")
        if not mutability or mutability.text != b"let":
            return None
        pattern = node.child_by_field_name("name")
        if not pattern:
            return None
        name_node = pattern.child_by_field_name("bound_identifier")
        if not name_node:
            # fallback: first simple_identifier in pattern
            for child in pattern.children:
                if child.type == "simple_identifier":
                    name_node = child
                    break
        if not name_node:
            return None
        name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        if not (name.isupper() or (len(name) > 1 and name[0].isupper() and "_" in name)):
            return None
        sig = source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()
        const_bytes = source_bytes[node.start_byte:node.end_byte]
        c_hash = compute_content_hash(const_bytes)
        return Symbol(
            id=make_symbol_id(filename, name, "constant"),
            file=filename,
            name=name,
            qualified_name=name,
            kind="constant",
            language=language,
            signature=sig[:100],
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            byte_offset=node.start_byte,
            byte_length=node.end_byte - node.start_byte,
            content_hash=c_hash,
        )

    return None


# ===========================================================================
# Elixir custom extractor
# ===========================================================================

def _get_elixir_args(node) -> Optional[object]:
    """Return the `arguments` named child of an Elixir AST node.

    The Elixir tree-sitter grammar does not expose `arguments` as a named
    field (only `target` is a named field on `call` nodes), so we find it by
    scanning named_children.
    """
    for child in node.named_children:
        if child.type == "arguments":
            return child
    return None


# --- Elixir keyword sets ---
_ELIXIR_MODULE_KW = frozenset({"defmodule", "defprotocol", "defimpl"})
_ELIXIR_FUNCTION_KW = frozenset({"def", "defp", "defmacro", "defmacrop", "defguard", "defguardp"})
_ELIXIR_TYPE_ATTRS = frozenset({"type", "typep", "opaque"})
_ELIXIR_SKIP_ATTRS = frozenset({"spec", "impl"})


def _node_text(node, source_bytes: bytes) -> str:
    """Return the decoded text of a tree-sitter node."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8").strip()


def _first_named_child(node):
    """Return the first named child of a node, or None."""
    return next((c for c in node.children if c.is_named), None)


def _get_elixir_attr_name(node, source_bytes: bytes) -> Optional[str]:
    """Extract the attribute name from a unary_operator `@attr` node, or None."""
    inner = _first_named_child(node)
    if inner and inner.type == "call":
        target = inner.child_by_field_name("target")
        if target:
            return _node_text(target, source_bytes)
    return None


def _make_elixir_symbol(
    node, source_bytes: bytes, filename: str, name: str, qualified_name: str,
    kind: str, parent_symbol: Optional[Symbol], signature: str, docstring: str = ""
) -> Symbol:
    """Construct a Symbol for an Elixir node."""
    symbol_bytes = source_bytes[node.start_byte:node.end_byte]
    return Symbol(
        id=make_symbol_id(filename, qualified_name, kind),
        file=filename,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        language="elixir",
        signature=signature,
        docstring=docstring,
        parent=parent_symbol.id if parent_symbol else None,
        line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        byte_offset=node.start_byte,
        byte_length=node.end_byte - node.start_byte,
        content_hash=compute_content_hash(symbol_bytes),
    )


def _parse_elixir_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse Elixir source and return extracted symbols."""
    spec = LANGUAGE_REGISTRY["elixir"]
    try:
        parser = get_parser(spec.ts_language)
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    symbols: list[Symbol] = []
    _walk_elixir(tree.root_node, source_bytes, filename, symbols, None)
    return symbols


def _walk_elixir(node, source_bytes: bytes, filename: str, symbols: list, parent_symbol: Optional[Symbol]):
    """Recursively walk Elixir AST and extract symbols."""
    if node.type == "call":
        target = node.child_by_field_name("target")
        if target is None:
            _walk_elixir_children(node, source_bytes, filename, symbols, parent_symbol)
            return

        keyword = _node_text(target, source_bytes)

        if keyword in _ELIXIR_MODULE_KW:
            sym = _extract_elixir_module(node, keyword, source_bytes, filename, parent_symbol)
            if sym:
                symbols.append(sym)
                # Recurse into do_block with this module as parent
                do_block = _find_elixir_do_block(node)
                if do_block:
                    _walk_elixir_children(do_block, source_bytes, filename, symbols, sym)
                return

        if keyword in _ELIXIR_FUNCTION_KW:
            sym = _extract_elixir_function(node, keyword, source_bytes, filename, parent_symbol)
            if sym:
                symbols.append(sym)
            return

    elif node.type == "unary_operator":
        inner_call = _first_named_child(node)
        if inner_call and inner_call.type == "call":
            inner_target = inner_call.child_by_field_name("target")
            if inner_target:
                attr_name = _node_text(inner_target, source_bytes)
                if attr_name in _ELIXIR_TYPE_ATTRS or attr_name == "callback":
                    sym = _extract_elixir_type_attribute(node, attr_name, inner_call, source_bytes, filename, parent_symbol)
                    if sym:
                        symbols.append(sym)
                    return

    _walk_elixir_children(node, source_bytes, filename, symbols, parent_symbol)


def _walk_elixir_children(node, source_bytes: bytes, filename: str, symbols: list, parent_symbol: Optional[Symbol]):
    for child in node.children:
        _walk_elixir(child, source_bytes, filename, symbols, parent_symbol)


def _find_elixir_do_block(call_node) -> Optional[object]:
    """Find the do_block child of a call node."""
    for child in call_node.children:
        if child.type == "do_block":
            return child
    return None


def _extract_elixir_module(node, keyword: str, source_bytes: bytes, filename: str, parent_symbol: Optional[Symbol]) -> Optional[Symbol]:
    """Extract a defmodule/defprotocol/defimpl symbol."""
    arguments = _get_elixir_args(node)
    if arguments is None:
        return None

    # For defimpl, find `alias` (implemented module) + `for:` target
    if keyword == "defimpl":
        name = _extract_elixir_defimpl_name(arguments, source_bytes, parent_symbol)
    else:
        name = _extract_elixir_alias_name(arguments, source_bytes)

    if not name:
        return None

    kind = "type" if keyword == "defprotocol" else "class"

    if parent_symbol:
        qualified_name = f"{parent_symbol.qualified_name}.{name}"
    else:
        qualified_name = name

    # Signature: everything up to the do_block
    signature = _build_elixir_signature(node, source_bytes)

    # Moduledoc: look inside do_block
    do_block = _find_elixir_do_block(node)
    docstring = _extract_elixir_moduledoc(do_block, source_bytes) if do_block else ""

    return _make_elixir_symbol(node, source_bytes, filename, name, qualified_name, kind, parent_symbol, signature, docstring)


def _extract_elixir_alias_name(arguments, source_bytes: bytes) -> Optional[str]:
    """Extract module name from an `alias` node in arguments."""
    for child in arguments.children:
        if child.type == "alias":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8").strip()
        # Sometimes the module name is an `atom` (rare) or `identifier`
        if child.type in ("identifier", "atom"):
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8").strip()
    return None


def _extract_elixir_defimpl_name(arguments, source_bytes: bytes, parent_symbol: Optional[Symbol]) -> Optional[str]:
    """Build a name for defimpl: '<Protocol>.<ForModule>' or just the protocol name."""
    # First child is usually the protocol alias
    proto_name = None
    for_name = None

    for child in arguments.children:
        if child.type == "alias" and proto_name is None:
            proto_name = source_bytes[child.start_byte:child.end_byte].decode("utf-8").strip()
        # `for:` keyword argument: keywords > pair > (atom "for") + alias
        if child.type == "keywords":
            for pair in child.children:
                if pair.type == "pair":
                    key_node = pair.child_by_field_name("key")
                    val_node = pair.child_by_field_name("value")
                    if key_node and val_node:
                        key_text = source_bytes[key_node.start_byte:key_node.end_byte].decode("utf-8").strip()
                        if key_text in ("for", "for:"):
                            for_name = source_bytes[val_node.start_byte:val_node.end_byte].decode("utf-8").strip()

    if proto_name and for_name:
        # e.g. Printable.Integer
        return f"{proto_name}.{for_name}"
    return proto_name


def _extract_elixir_function(node, keyword: str, source_bytes: bytes, filename: str, parent_symbol: Optional[Symbol]) -> Optional[Symbol]:
    """Extract a def/defp/defmacro/defmacrop/defguard/defguardp symbol."""
    arguments = _get_elixir_args(node)
    if arguments is None:
        return None

    # First named child in arguments is a `call` node (the function head)
    func_call = _first_named_child(arguments)
    if func_call is None:
        return None

    # Handle guard: `def foo(x) when is_integer(x)` — binary_operator `when`
    actual_call = func_call
    if func_call.type == "binary_operator":
        left = func_call.child_by_field_name("left")
        if left:
            actual_call = left

    name = _extract_elixir_call_name(actual_call, source_bytes)
    if not name:
        return None

    # Determine kind based on parent context
    if parent_symbol and parent_symbol.kind in ("class", "type"):
        kind = "method"
    else:
        kind = "function"

    if parent_symbol:
        qualified_name = f"{parent_symbol.qualified_name}.{name}"
    else:
        qualified_name = name

    signature = _build_elixir_signature(node, source_bytes)
    docstring = _extract_elixir_doc(node, source_bytes)

    return _make_elixir_symbol(node, source_bytes, filename, name, qualified_name, kind, parent_symbol, signature, docstring)


def _extract_elixir_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    """Extract the function name from a call node's target."""
    if call_node.type == "call":
        target = call_node.child_by_field_name("target")
        if target:
            return source_bytes[target.start_byte:target.end_byte].decode("utf-8").strip()
    if call_node.type == "identifier":
        return source_bytes[call_node.start_byte:call_node.end_byte].decode("utf-8").strip()
    return None


def _build_elixir_signature(node, source_bytes: bytes) -> str:
    """Build function/module signature: text up to the do_block."""
    do_block = _find_elixir_do_block(node)
    if do_block:
        sig_bytes = source_bytes[node.start_byte:do_block.start_byte]
    else:
        sig_bytes = source_bytes[node.start_byte:node.end_byte]
    return sig_bytes.decode("utf-8").strip().rstrip(",").strip()


def _extract_elixir_doc(node, source_bytes: bytes) -> str:
    """Walk backward through prev_named_sibling looking for @doc attribute."""
    prev = node.prev_named_sibling
    while prev is not None:
        if prev.type == "unary_operator":
            attr = _get_elixir_attr_name(prev, source_bytes)
            if attr == "doc":
                inner = _first_named_child(prev)
                return _extract_elixir_string_arg(inner, source_bytes)
            if attr in _ELIXIR_SKIP_ATTRS:
                # Skip @spec and @impl, keep walking back
                prev = prev.prev_named_sibling
                continue
            # Some other attribute — stop
            break
        elif prev.type == "comment":
            prev = prev.prev_named_sibling
            continue
        else:
            break
    return ""


def _extract_elixir_moduledoc(do_block, source_bytes: bytes) -> str:
    """Find @moduledoc inside a do_block and extract its string content."""
    if do_block is None:
        return ""
    for child in do_block.children:
        if child.type == "unary_operator":
            if _get_elixir_attr_name(child, source_bytes) == "moduledoc":
                inner = _first_named_child(child)
                return _extract_elixir_string_arg(inner, source_bytes)
    return ""


def _extract_elixir_string_arg(call_node, source_bytes: bytes) -> str:
    """Extract string content from @doc/@moduledoc argument (handles both "" and \"\"\"\"\"\")."""
    arguments = _get_elixir_args(call_node)
    if arguments is None:
        return ""

    for child in arguments.children:
        if child.type == "string":
            text = source_bytes[child.start_byte:child.end_byte].decode("utf-8")
            return _strip_quotes(text)
        # @doc false → boolean node, not a string
    return ""


def _extract_elixir_type_attribute(node, attr_name: str, inner_call, source_bytes: bytes, filename: str, parent_symbol: Optional[Symbol]) -> Optional[Symbol]:
    """Extract @type/@typep/@opaque as type symbols."""
    # inner_call is the `call` inside `@type name :: expr`
    arguments = _get_elixir_args(inner_call)
    if arguments is None:
        return None

    # The first named child is a `binary_operator` with `::` operator
    # whose left side is the type name (possibly a call for parameterized types)
    for child in arguments.children:
        if child.is_named:
            name = _extract_elixir_type_name(child, source_bytes)
            if not name:
                return None

            kind = "type"
            if parent_symbol:
                qualified_name = f"{parent_symbol.qualified_name}.{name}"
            else:
                qualified_name = name

            sig = _node_text(node, source_bytes)
            return _make_elixir_symbol(node, source_bytes, filename, name, qualified_name, kind, parent_symbol, sig)
    return None


def _extract_elixir_type_name(type_expr_node, source_bytes: bytes) -> Optional[str]:
    """Extract just the name from a type expression like `name :: type` or `name(params) :: type`."""
    # `binary_operator` with `::` — left side is the name
    if type_expr_node.type == "binary_operator":
        left = type_expr_node.child_by_field_name("left")
        if left:
            return _extract_elixir_type_name(left, source_bytes)
    # Plain `call` like `name(params)` — name is the target
    if type_expr_node.type == "call":
        target = type_expr_node.child_by_field_name("target")
        if target:
            return source_bytes[target.start_byte:target.end_byte].decode("utf-8").strip()
    # Plain identifier
    if type_expr_node.type in ("identifier", "atom"):
        return source_bytes[type_expr_node.start_byte:type_expr_node.end_byte].decode("utf-8").strip()
    return None


def _disambiguate_overloads(symbols: list[Symbol]) -> list[Symbol]:
    """Append ordinal suffix to symbols with duplicate IDs.

    E.g., if two symbols have ID "file.py::foo#function", they become
    "file.py::foo#function~1" and "file.py::foo#function~2".
    """
    from collections import Counter

    id_counts = Counter(s.id for s in symbols)
    # Only process IDs that appear more than once
    duplicated = {sid for sid, count in id_counts.items() if count > 1}

    if not duplicated:
        return symbols

    # Track ordinals per duplicate ID
    ordinals: dict[str, int] = {}
    result = []
    for sym in symbols:
        if sym.id in duplicated:
            ordinals[sym.id] = ordinals.get(sym.id, 0) + 1
            sym.id = f"{sym.id}~{ordinals[sym.id]}"
        result.append(sym)
    return result


# ---------------------------------------------------------------------------
# Blade template parser (regex-based; no tree-sitter grammar available)
# ---------------------------------------------------------------------------

_BLADE_SYMBOL_PATTERNS: list[tuple[str, str, str]] = [
    ("type",     r"@extends\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("method",   r"@section\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("class",    r"@component\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("function", r"@include(?:If|When|Unless|First)?\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("constant", r"@push\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("constant", r"@stack\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("method",   r"@slot\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("method",   r"@yield\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    ("class",    r"@livewire\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
]

# ---------------------------------------------------------------------------
# Verse (UEFN) — regex-based symbol extraction for Epic's Verse language
# ---------------------------------------------------------------------------
#
# No tree-sitter grammar exists for Verse, so this parser uses regex with a
# multi-pass strategy similar to the Blade parser above.
#
# PRIMARY USE CASE: Token-efficient lookup of UEFN API digest files.
#
# Epic ships Fortnite/UEFN API definitions as `.verse` digest files that are
# very large (the three standard digest files total ~800KB / ~200k tokens):
#
#   Fortnite.digest.verse    587KB  12,258 lines  3,608 symbols  ~147k tokens
#   Verse.digest.verse       125KB   2,368 lines    622 symbols   ~31k tokens
#   UnrealEngine.digest.verse 91KB   1,495 lines    326 symbols   ~23k tokens
#
# Loading even one of these into an LLM context window is expensive.
# With jcodemunch indexing, a typical symbol lookup returns ~94 tokens
# instead of ~147,000 — a 99.9% reduction. A search returning 10 signature
# matches costs ~130 tokens vs the full file's ~147k.
#
# ARCHITECTURE:
#
# Verse uses indentation-based scoping with a distinctive declaration syntax:
#
#   name<specifiers> := kind<specifiers>(parents):
#       member<specifiers>(...)<effects>:return_type
#       var Name<specifiers>:type
#
# Extension methods use receiver syntax:
#   (Param:type).MethodName<specifiers>()<effects>:return_type
#
# Digest files use path-prefixed declarations for namespace qualification:
#   (/Fortnite.com:)UI<public> := module:
#
# Decorators use @attribute syntax:
#   @editable
#   @available {MinUploadedAtFNVersion := 3800}
#
# The parser runs in 5 passes to handle declaration priority correctly:
#   Pass 1: Container definitions (module, class, interface, struct, enum, trait)
#   Pass 2: Extension methods — (Receiver:type).Method() syntax
#   Pass 3: Regular methods — indented Name(params) inside containers
#   Pass 4: Variables — var Name:type declarations
#   Pass 5: Constants — Name:type = value assignments
#
# IMPORTANT — Character vs byte offset handling:
#
# Python regex operates on decoded strings where multi-byte UTF-8 characters
# (e.g., smart quotes U+2019 = 3 bytes) count as 1 character. But the
# retrieval path (get_symbol_content) does binary f.seek(byte_offset), so
# stored byte_offset values MUST be real byte positions — not character
# positions. The char_pos_to_byte_pos() helper handles this conversion.
# The Verse digest files contain multi-byte UTF-8 characters in docstrings
# (smart quotes), which affects ~60% of all extracted symbols.

# Shared regex fragment for Verse specifiers like <public>, <native><override>
_VERSE_SPECS = r'(?:<[a-z_]+>)*'

# --- Pass 1 regex: Container definitions ---
# Matches: name<specs> := kind<specs>(parents):
# Also:    (/Fortnite.com:)name<specs> := module:
_VERSE_DEF_RE = re.compile(
    r'^([ \t]*)'                                   # (1) indentation — [ \t] only, NOT \s (which captures \n in MULTILINE)
    r'(?:\([^)]*:\))?'                             # optional path prefix e.g. (/Fortnite.com:)
    r'([\w]+)'                                     # (2) name
    r'(' + _VERSE_SPECS + r')'                     # (3) specifiers e.g. <public><native>
    r'\s*:=\s*'                                    # := assignment operator
    r'(module|class|interface|struct|enum|trait)'   # (4) kind keyword
    r'(' + _VERSE_SPECS + r')'                     # (5) kind specifiers e.g. <concrete>
    r'(?:\(([^)]*)\))?'                            # (6) optional parent types e.g. (base_class)
    r'\s*:',                                       # trailing colon (starts indented block)
    re.MULTILINE,
)

# --- Pass 3 regex: Method/function members ---
# Matches: Name<specs>(params)<effects>:return_type
# Also:    (/Path:)Name<specs>(...)
_VERSE_METHOD_RE = re.compile(
    r'^([ \t]+)'                                   # (1) indentation — must be indented (inside a container)
    r'(?:\([^)]*:\))?'                             # optional path prefix
    r'([\w]+)'                                     # (2) name
    r'(' + _VERSE_SPECS + r')'                     # (3) specifiers
    r'\(([^)]*)\)'                                 # (4) parameters
    r'(' + _VERSE_SPECS + r')'                     # (5) effect specifiers e.g. <decides><transacts>
    r'(?::(\S+))?'                                 # (6) optional return type
    r'.*$',                                        # rest of line (may contain = external {})
    re.MULTILINE,
)

# --- Pass 2 regex: Extension methods ---
# Matches: (Param:type).Name<specs>(params)<effects>:return_type
_VERSE_EXT_METHOD_RE = re.compile(
    r'^([ \t]*)'                                   # (1) indentation
    r'\(([^)]+)\)'                                 # (2) receiver e.g. (InCharacter:fort_character)
    r'\.([\w]+)'                                   # (3) method name after dot
    r'(' + _VERSE_SPECS + r')'                     # (4) specifiers
    r'\(([^)]*)\)'                                 # (5) parameters
    r'(' + _VERSE_SPECS + r')'                     # (6) effect specifiers
    r'(?::(\S+))?'                                 # (7) optional return type
    r'.*$',
    re.MULTILINE,
)

# --- Pass 4 regex: Variable declarations ---
# Matches: var Name<specs>:type  or  var<private> Name:type
_VERSE_VAR_RE = re.compile(
    r'^([ \t]+)'                                   # (1) indentation (must be inside container)
    r'var(?:<[a-z_]+>)?'                           # var keyword with optional specifier
    r'\s+'
    r'([\w]+)'                                     # (2) name
    r'(' + _VERSE_SPECS + r')'                     # (3) specifiers
    r':([^\s=]+)'                                  # (4) type (up to whitespace or =)
    r'.*$',
    re.MULTILINE,
)

# --- Pass 5 regex: Constants/values ---
# Matches: Name<specs>:type = ...
# Also:    (/Path:)Name<specs>:type = external {}
_VERSE_CONST_RE = re.compile(
    r'^([ \t]+)'                                   # (1) indentation (must be inside container)
    r'(?:\([^)]*:\))?'                             # optional path prefix
    r'([\w]+)'                                     # (2) name
    r'(' + _VERSE_SPECS + r')'                     # (3) specifiers
    r':(\S+)'                                      # (4) type
    r'\s*=\s*'                                     # = assignment
    r'.*$',
    re.MULTILINE,
)

# Enum value (simple identifier on its own line — currently unused, reserved for future)
_VERSE_ENUM_VAL_RE = re.compile(
    r'^(\s+)'                                      # (1) indentation
    r'([\w]+)'                                     # (2) name
    r'\s*$',
    re.MULTILINE,
)

# Module import path comment: # Module import path: /Something/Path
_VERSE_MODULE_PATH_RE = re.compile(
    r'#\s*Module import path:\s*(\S+)',
)

# Decorator line: @editable, @available {MinUploadedAtFNVersion := 3800}
_VERSE_DECORATOR_RE = re.compile(
    r'^(\s*)@(\w+)\s*(.*?)$',
    re.MULTILINE,
)


def _parse_verse_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Verse (UEFN) source files using regex.

    Designed for Epic's Verse API digest files (Fortnite.digest.verse,
    Verse.digest.verse, UnrealEngine.digest.verse). These files define the
    entire UEFN API surface — thousands of classes, methods, and constants —
    and are too large to load into an LLM context window directly (~200k
    tokens for all three). Indexing them with jcodemunch reduces a typical
    symbol lookup from ~147,000 tokens to ~94 tokens (99.9% savings).

    The parser runs in 5 ordered passes so earlier passes take priority over
    later ones via seen_ids deduplication:

      Pass 1: Container definitions (module, class, interface, struct, enum)
      Pass 2: Extension methods — (Receiver:type).Method() syntax
      Pass 3: Regular methods — indented Name(params) inside containers
      Pass 4: Variable declarations — var Name:type
      Pass 5: Constants — Name:type = value

    Parent-child relationships are determined by line-range containment: each
    container records its start/end line, and members are assigned to the
    innermost container whose line range encloses them and whose indentation
    is less than the member's.

    Args:
        source_bytes: Raw file content (binary). Used for byte-offset
            calculation and content hashing.
        filename: The file's path/name for symbol IDs.

    Returns:
        List of Symbol objects sorted by line number, with correct
        byte_offset/byte_length for binary file seeking.
    """
    content = source_bytes.decode("utf-8", errors="replace")
    lines = content.splitlines()

    # ── Dual offset tables (char-based and byte-based) ──────────────────
    #
    # Why two tables? Python regex .start() returns CHARACTER positions in
    # the decoded string, but get_symbol_content() does f.seek(byte_offset)
    # in binary mode — it needs BYTE positions.
    #
    # For pure ASCII files these are identical. But the Verse digest files
    # contain multi-byte UTF-8 characters (e.g., smart quotes U+2019 = 3
    # bytes \xe2\x80\x99 in docstrings). In Fortnite.digest.verse, ~60% of
    # symbols appear after such characters, so their char offset diverges
    # from their byte offset. Without this conversion, get_symbol_content()
    # would seek to the wrong file position and return corrupted content.
    char_line_starts: list[int] = []  # cumulative character offset per line
    byte_line_starts: list[int] = []  # cumulative byte offset per line
    char_off = 0
    byte_off = 0
    for line in lines:
        char_line_starts.append(char_off)
        byte_line_starts.append(byte_off)
        char_off += len(line) + 1              # +1 for \n (char count)
        byte_off += len(line.encode("utf-8")) + 1  # +1 for \n (byte count)

    def char_to_line(char_pos: int) -> int:
        """Map a character offset (from regex .start()) to a 1-indexed line number.

        Uses binary search over char_line_starts for O(log n) lookup.
        """
        lo, hi = 0, len(char_line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if char_line_starts[mid] <= char_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1  # 1-indexed

    def char_pos_to_byte_pos(char_pos: int) -> int:
        """Convert a character offset (from regex .start()) to a real byte offset.

        This is the critical bridge between regex (which operates on decoded
        Python strings) and file I/O (which operates on raw bytes). The
        algorithm:
          1. Binary-search char_line_starts to find which line char_pos is on
          2. Compute how many chars into that line: char_pos - line_char_start
          3. Encode just that line prefix to UTF-8 to get exact byte count
          4. Return: byte_line_start + encoded_prefix_byte_length

        This matches tree-sitter's node.start_byte behavior for languages
        that have tree-sitter grammars.
        """
        # Find the 0-based line index via binary search
        lo, hi = 0, len(char_line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if char_line_starts[mid] <= char_pos:
                lo = mid
            else:
                hi = mid - 1
        line_idx = lo
        # Encode the chars before char_pos on this line to get byte count
        char_into_line = char_pos - char_line_starts[line_idx]
        line_prefix = lines[line_idx][:char_into_line]
        return byte_line_starts[line_idx] + len(line_prefix.encode("utf-8"))

    # ── Docstring and decorator extraction ──────────────────────────────
    #
    # Verse uses # line comments for documentation and @attribute for
    # decorators. Both appear on lines immediately above a declaration.
    # We walk upward from the declaration line, skipping decorators when
    # gathering comments (and vice versa).

    def _get_preceding_comment(line_idx: int) -> str:
        """Gather # comment lines immediately above line_idx (0-indexed).

        Walks upward, collecting comment text and skipping @decorator lines
        that may be interspersed. Returns joined text with # prefix stripped.
        """
        doc_lines: list[str] = []
        i = line_idx - 1
        while i >= 0:
            stripped = lines[i].strip()
            if stripped.startswith("#"):
                doc_lines.append(stripped.lstrip("# ").strip())
                i -= 1
            elif stripped.startswith("@"):
                i -= 1  # decorators can appear between comment and declaration
            else:
                break
        doc_lines.reverse()
        return "\n".join(doc_lines)

    def _get_decorators(line_idx: int) -> list[str]:
        """Gather @decorator lines immediately above line_idx (0-indexed).

        Walks upward, collecting decorator text and skipping # comment lines.
        Returns decorators in source order (top to bottom).
        """
        decs: list[str] = []
        i = line_idx - 1
        while i >= 0:
            stripped = lines[i].strip()
            if stripped.startswith("@"):
                decs.append(stripped)
                i -= 1
            elif stripped.startswith("#"):
                i -= 1  # skip comments between decorators
            else:
                break
        decs.reverse()
        return decs

    # ── Indentation-based block detection ───────────────────────────────

    def _find_block_end(start_line_idx: int, base_indent: int) -> int:
        """Find the last line of an indentation block starting at start_line_idx.

        Verse uses indentation for scoping (like Python). A block ends when
        a non-blank, non-comment line appears at the base indentation level
        or less. Blank lines, comments, and decorator lines are skipped
        (they don't terminate a block).

        Returns: 0-indexed line number of the last line in the block.
        """
        last = start_line_idx
        for i in range(start_line_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("@"):
                continue  # blank, comment, or decorator lines don't end blocks
            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent <= base_indent:
                break
            last = i
        return last

    # ── Symbol collection state ─────────────────────────────────────────

    symbols: list[Symbol] = []
    seen_ids: set[str] = set()  # prevents duplicates across passes

    # Containers track: (indent, qualified_name, kind_raw, start_line, end_line)
    # Used for parent assignment via line-range containment. This approach
    # correctly handles sibling containers at the same indent level — a
    # pure indent-only strategy would incorrectly assign members of a later
    # container to an earlier sibling.
    containers: list[tuple[int, str, str, int, int]] = []

    def _find_parent(member_line_1idx: int, member_indent: int) -> "Optional[str]":
        """Find the innermost container enclosing this member.

        Uses both indentation (member must be more indented than container)
        and line-range containment (member line must fall within container's
        start..end range). When multiple containers qualify, picks the one
        with the greatest indentation (innermost nesting).

        Args:
            member_line_1idx: 1-indexed line number of the member.
            member_indent: Column indentation of the member.

        Returns:
            Qualified name of the parent container, or None if top-level.
        """
        best = None
        for _indent, cname, _ckind, cstart, cend in containers:
            if member_indent > _indent and cstart <= member_line_1idx <= cend:
                if best is None or _indent > best[0]:
                    best = (_indent, cname)
        return best[1] if best else None

    # Optional module path from header comment (e.g., # Module import path: /Verse.org/...)
    module_path = ""
    mp_match = _VERSE_MODULE_PATH_RE.search(content)
    if mp_match:
        module_path = mp_match.group(1)

    # ── Pass 1: Container definitions ───────────────────────────────────
    #
    # Extracts module, class, interface, struct, enum, and trait declarations.
    # These are the "containers" that hold methods, vars, and constants.
    # Must run first so containers[] is populated for parent lookups in
    # later passes.
    #
    # Containers store byte_offset/byte_length spanning their FULL block
    # (declaration line through last indented member), so get_symbol()
    # returns the complete definition including all members.

    for m in _VERSE_DEF_RE.finditer(content):
        indent_str = m.group(1)
        indent = len(indent_str)
        name = m.group(2)
        specs = m.group(3)
        kind_raw = m.group(4)
        kind_specs = m.group(5)
        parents = m.group(6) or ""

        # Use group(2) (the name) for line lookup — group(1) is indentation,
        # and m.start(0) could include characters from a prior line due to
        # ^ anchor behavior in MULTILINE mode with [ \t]* matching empty.
        line_idx = char_to_line(m.start(2)) - 1  # 0-indexed
        end_line_idx = _find_block_end(line_idx, indent)

        # Map Verse declaration kinds to jcodemunch symbol kinds.
        # Modules map to "class" because they act as namespaces/containers.
        kind_map = {
            "module": "class",
            "class": "class",
            "interface": "type",
            "struct": "type",
            "enum": "type",
            "trait": "type",
        }
        kind = kind_map.get(kind_raw, "type")

        sig_parts = [f"{name}{specs} := {kind_raw}{kind_specs}"]
        if parents:
            sig_parts.append(f"({parents})")
        signature = "".join(sig_parts)

        docstring = _get_preceding_comment(line_idx)
        decorators = _get_decorators(line_idx)

        parent_name = _find_parent(line_idx + 1, indent)

        qualified = f"{parent_name}.{name}" if parent_name else name
        sym_id = make_symbol_id(filename, qualified, kind)

        if sym_id not in seen_ids:
            seen_ids.add(sym_id)
            match_bytes = m.group(0).encode("utf-8")

            # Compute byte range for the entire container block.
            # block_byte_start = byte position of the declaration line.
            # block_byte_end = end of the last indented member line.
            block_byte_start = char_pos_to_byte_pos(m.start())
            if end_line_idx < len(byte_line_starts):
                block_byte_end = byte_line_starts[end_line_idx] + len(lines[end_line_idx].encode("utf-8"))
            else:
                block_byte_end = block_byte_start + len(match_bytes)

            symbols.append(Symbol(
                id=sym_id,
                file=filename,
                name=name,
                qualified_name=qualified,
                kind=kind,
                language="verse",
                signature=signature,
                docstring=docstring,
                decorators=decorators,
                parent=make_symbol_id(filename, parent_name, "class") if parent_name else None,
                line=line_idx + 1,
                end_line=end_line_idx + 1,
                byte_offset=block_byte_start,
                byte_length=block_byte_end - block_byte_start,
                content_hash=compute_content_hash(source_bytes[block_byte_start:block_byte_end]),
            ))

        # Register container for parent lookups in passes 2-5
        containers.append((indent, qualified, kind_raw, line_idx + 1, end_line_idx + 1))

    # ── Pass 2: Extension methods ───────────────────────────────────────
    #
    # Verse extension methods use receiver syntax:
    #   (InPlayer:player).GetScore<public>()<transacts>:int
    #
    # These are matched separately because they have a distinctive
    # (Receiver:type).Name pattern that doesn't overlap with regular methods.

    for m in _VERSE_EXT_METHOD_RE.finditer(content):
        indent_str = m.group(1)
        indent = len(indent_str)
        receiver = m.group(2)
        name = m.group(3)
        specs = m.group(4)
        params = m.group(5)
        effects = m.group(6)
        ret_type = m.group(7) or ""

        line_idx = char_to_line(m.start(2)) - 1
        sig = f"({receiver}).{name}{specs}({params}){effects}"
        if ret_type:
            sig += f":{ret_type}"

        # Qualified name uses the receiver type (e.g., player.GetScore)
        recv_type = receiver.split(":")[-1].strip() if ":" in receiver else receiver
        qualified = f"{recv_type}.{name}"

        # Extension methods can appear inside module blocks
        parent_name = _find_parent(line_idx + 1, indent)
        if parent_name:
            qualified = f"{parent_name}.{name}"

        sym_id = make_symbol_id(filename, qualified, "method")

        if sym_id not in seen_ids:
            seen_ids.add(sym_id)
            docstring = _get_preceding_comment(line_idx)
            decorators = _get_decorators(line_idx)
            match_bytes = m.group(0).encode("utf-8")

            symbols.append(Symbol(
                id=sym_id,
                file=filename,
                name=name,
                qualified_name=qualified,
                kind="method",
                language="verse",
                signature=sig,
                docstring=docstring,
                decorators=decorators,
                parent=make_symbol_id(filename, parent_name, "class") if parent_name else None,
                line=line_idx + 1,
                end_line=line_idx + 1,
                byte_offset=char_pos_to_byte_pos(m.start()),
                byte_length=len(match_bytes),
                content_hash=compute_content_hash(match_bytes),
            ))

    # ── Pass 3: Regular methods inside containers ───────────────────────
    #
    # Matches indented Name(params) declarations that weren't already
    # captured as container definitions (Pass 1) or extension methods
    # (Pass 2). Requires a parent container — top-level functions with
    # params would be unusual in digest files and are skipped.

    for m in _VERSE_METHOD_RE.finditer(content):
        indent_str = m.group(1)
        indent = len(indent_str)
        name = m.group(2)
        specs = m.group(3)
        params = m.group(4)
        effects = m.group(5)
        ret_type = m.group(6) or ""

        line_idx = char_to_line(m.start(2)) - 1

        # Guard: skip lines already handled by other passes
        full_line = lines[line_idx].strip() if line_idx < len(lines) else ""
        if ":=" in full_line:
            continue  # definition line (Pass 1)
        if full_line.startswith("var"):
            continue  # variable declaration (Pass 4)

        parent_name = _find_parent(line_idx + 1, indent)

        if not parent_name:
            continue  # methods must be inside a container

        qualified = f"{parent_name}.{name}"
        kind = "method"
        sym_id = make_symbol_id(filename, qualified, kind)

        if sym_id not in seen_ids:
            seen_ids.add(sym_id)
            sig = f"{name}{specs}({params}){effects}"
            if ret_type:
                sig += f":{ret_type}"

            docstring = _get_preceding_comment(line_idx)
            decorators = _get_decorators(line_idx)
            match_bytes = m.group(0).encode("utf-8")

            symbols.append(Symbol(
                id=sym_id,
                file=filename,
                name=name,
                qualified_name=qualified,
                kind=kind,
                language="verse",
                signature=sig,
                docstring=docstring,
                decorators=decorators,
                parent=make_symbol_id(filename, parent_name, "class") if parent_name else None,
                line=line_idx + 1,
                end_line=line_idx + 1,
                byte_offset=char_pos_to_byte_pos(m.start()),
                byte_length=len(match_bytes),
                content_hash=compute_content_hash(match_bytes),
            ))

    # ── Pass 4: Variable declarations ───────────────────────────────────
    #
    # Matches: var Name<specs>:type
    # Stored as "constant" kind (jcodemunch doesn't distinguish var/const).

    for m in _VERSE_VAR_RE.finditer(content):
        indent_str = m.group(1)
        indent = len(indent_str)
        name = m.group(2)
        specs = m.group(3)
        var_type = m.group(4)

        line_idx = char_to_line(m.start(2)) - 1

        parent_name = _find_parent(line_idx + 1, indent)

        qualified = f"{parent_name}.{name}" if parent_name else name
        sym_id = make_symbol_id(filename, qualified, "constant")

        if sym_id not in seen_ids:
            seen_ids.add(sym_id)
            sig = f"var {name}{specs}:{var_type}"
            docstring = _get_preceding_comment(line_idx)
            match_bytes = m.group(0).encode("utf-8")

            symbols.append(Symbol(
                id=sym_id,
                file=filename,
                name=name,
                qualified_name=qualified,
                kind="constant",
                language="verse",
                signature=sig,
                docstring=docstring,
                parent=make_symbol_id(filename, parent_name, "class") if parent_name else None,
                line=line_idx + 1,
                end_line=line_idx + 1,
                byte_offset=char_pos_to_byte_pos(m.start()),
                byte_length=len(match_bytes),
                content_hash=compute_content_hash(match_bytes),
            ))

    # ── Pass 5: Constants and value declarations ────────────────────────
    #
    # Matches: Name<specs>:type = external {}
    # This is the most common pattern in digest files for API surface
    # declarations. Runs last so vars (Pass 4) and definitions (Pass 1)
    # take priority via seen_ids.

    for m in _VERSE_CONST_RE.finditer(content):
        indent_str = m.group(1)
        indent = len(indent_str)
        name = m.group(2)
        specs = m.group(3)
        const_type = m.group(4)

        line_idx = char_to_line(m.start(2)) - 1

        # Guard: skip lines handled by earlier passes
        full_line = lines[line_idx].strip() if line_idx < len(lines) else ""
        if full_line.startswith("var"):
            continue  # var declaration (Pass 4)
        if ":=" in full_line:
            continue  # definition line (Pass 1)

        parent_name = _find_parent(line_idx + 1, indent)

        qualified = f"{parent_name}.{name}" if parent_name else name
        sym_id = make_symbol_id(filename, qualified, "constant")

        if sym_id not in seen_ids:
            seen_ids.add(sym_id)
            sig = f"{name}{specs}:{const_type}"
            docstring = _get_preceding_comment(line_idx)
            match_bytes = m.group(0).encode("utf-8")

            symbols.append(Symbol(
                id=sym_id,
                file=filename,
                name=name,
                qualified_name=qualified,
                kind="constant",
                language="verse",
                signature=sig,
                docstring=docstring,
                parent=make_symbol_id(filename, parent_name, "class") if parent_name else None,
                line=line_idx + 1,
                end_line=line_idx + 1,
                byte_offset=char_pos_to_byte_pos(m.start()),
                byte_length=len(match_bytes),
                content_hash=compute_content_hash(match_bytes),
            ))

    symbols.sort(key=lambda s: s.line)
    return symbols


_BLADE_COMPILED: list[tuple[str, re.Pattern, str]] = [
    (kind, re.compile(pattern, re.IGNORECASE), group)
    for kind, pattern, group in _BLADE_SYMBOL_PATTERNS
]


def _parse_blade_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract Blade template symbols using regex.

    Scans for directives that define meaningful structural elements:
    @extends, @section, @component, @include*, @push, @stack, @slot,
    @yield, @livewire. No tree-sitter grammar exists for Blade.
    """
    content = source_bytes.decode("utf-8", errors="replace")
    lines = content.splitlines()

    line_start_offsets: list[int] = []
    offset = 0
    for line in lines:
        line_start_offsets.append(offset)
        offset += len(line.encode("utf-8")) + 1

    def byte_to_line(byte_pos: int) -> int:
        lo, hi = 0, len(line_start_offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_start_offsets[mid] <= byte_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    symbols: list[Symbol] = []
    seen: set[tuple[str, str]] = set()

    for kind, pattern, group in _BLADE_COMPILED:
        for m in pattern.finditer(content):
            name = m.group(group)
            key = (kind, name)
            if key in seen:
                continue
            seen.add(key)

            line_no = byte_to_line(m.start())
            directive_text = m.group(0)
            sym_bytes = directive_text.encode("utf-8")
            symbols.append(Symbol(
                id=make_symbol_id(filename, name, kind),
                file=filename,
                name=name,
                qualified_name=name,
                kind=kind,
                language="blade",
                signature=directive_text,
                docstring="",
                parent=None,
                line=line_no,
                end_line=line_no,
                byte_offset=m.start(),
                byte_length=len(sym_bytes),
                content_hash=compute_content_hash(sym_bytes),
            ))

    symbols.sort(key=lambda s: s.line)
    return symbols


# ---------------------------------------------------------------------------
# Nix custom symbol extractor
# ---------------------------------------------------------------------------

def _parse_nix_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Nix expression files.

    Nix is a pure expression language; all definitions are `binding` nodes
    inside `binding_set` children of `let_expression` or `attrset_expression`.
    We walk up to MAX_DEPTH levels deep and extract bindings whose attrpath is
    a single identifier (i.e. not a dotted path like `environment.packages`).
    Bindings whose RHS is a `function_expression` are classified as functions;
    all others are classified as constants.
    """
    from tree_sitter_language_pack import get_parser as _get_parser
    parser = _get_parser("nix")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []
    _walk_nix_bindings(tree.root_node, source_bytes, filename, symbols, depth=0)
    symbols.sort(key=lambda s: s.line)
    return symbols


def _walk_nix_bindings(node, source_bytes: bytes, filename: str, symbols: list, depth: int) -> None:
    """Recursively walk Nix AST, extracting bindings as symbols."""
    MAX_DEPTH = 4
    if depth > MAX_DEPTH:
        return

    for child in node.children:
        if child.type == "binding":
            _extract_nix_binding(child, source_bytes, filename, symbols)
        elif child.type in ("binding_set", "let_expression", "attrset_expression", "source_code"):
            _walk_nix_bindings(child, source_bytes, filename, symbols, depth + 1)


def _extract_nix_binding(node, source_bytes: bytes, filename: str, symbols: list) -> None:
    """Extract a single Nix binding as a Symbol if it has a simple (non-dotted) name."""
    attrpath_node = node.child_by_field_name("attrpath")
    expr_node = node.child_by_field_name("expression")
    if not attrpath_node or not expr_node:
        return

    # Only extract simple identifiers, skip dotted paths like `meta.description`
    name_children = [c for c in attrpath_node.children if c.is_named]
    if len(name_children) != 1 or name_children[0].type != "identifier":
        return

    name = source_bytes[name_children[0].start_byte:name_children[0].end_byte].decode("utf-8")

    kind = "function" if expr_node.type == "function_expression" else "constant"

    # Signature: binding up to (not including) the expression, + first line of RHS
    eq_end = expr_node.start_byte
    lhs = source_bytes[node.start_byte:eq_end].decode("utf-8").strip().rstrip("=").strip()
    rhs_first = source_bytes[expr_node.start_byte:expr_node.end_byte].decode("utf-8").splitlines()[0].strip()
    if len(rhs_first) > 60:
        rhs_first = rhs_first[:60] + "..."
    signature = f"{lhs} = {rhs_first}"

    # Docstring: preceding comment sibling.
    # In Nix, comments before the first binding in a binding_set appear as
    # siblings of the binding_set itself (inside let_expression), not of the
    # binding, so we also check the parent node's preceding sibling.
    docstring = ""
    comment_lines = []
    prev = node.prev_named_sibling
    while prev and prev.type == "comment":
        comment_lines.insert(0, source_bytes[prev.start_byte:prev.end_byte].decode("utf-8"))
        prev = prev.prev_named_sibling
    if not comment_lines and node.prev_named_sibling is None and node.parent:
        prev = node.parent.prev_named_sibling
        while prev and prev.type == "comment":
            comment_lines.insert(0, source_bytes[prev.start_byte:prev.end_byte].decode("utf-8"))
            prev = prev.prev_named_sibling
    if comment_lines:
        docstring = _clean_comment_markers("\n".join(comment_lines))

    sym_bytes = source_bytes[node.start_byte:node.end_byte]
    row, _ = node.start_point
    end_row, _ = node.end_point

    symbols.append(Symbol(
        id=make_symbol_id(filename, name, kind),
        file=filename,
        name=name,
        qualified_name=name,
        kind=kind,
        language="nix",
        signature=signature,
        docstring=docstring,
        parent=None,
        line=row + 1,
        end_line=end_row + 1,
        byte_offset=node.start_byte,
        byte_length=len(sym_bytes),
        content_hash=compute_content_hash(sym_bytes),
    ))


# ---------------------------------------------------------------------------
# Vue SFC custom symbol extractor
# ---------------------------------------------------------------------------

def _parse_vue_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Vue Single-File Components (.vue).

    Handles both Composition API (<script setup>) and Options API (<script>):

    Composition API:
      - Component name from filename (kind=class)
      - function declarations → kind=function
      - const X = ref/reactive/computed/watch... → kind=constant
      - const props = defineProps() / defineEmits() / defineExpose() → kind=constant
      - Preceding // or /* */ comments as docstrings

    Options API:
      - Component name from filename (kind=class)
      - methods: { X() } → kind=method
      - computed: { X() } → kind=method
      - props: [...] or props: {} → kind=constant (group)
      - data() → kind=function

    Line numbers are offset to match positions in the original .vue file.
    """
    from pathlib import Path as _Path
    from tree_sitter_language_pack import get_parser as _get_parser

    vue_parser = _get_parser("vue")
    tree = vue_parser.parse(source_bytes)

    # Find the first <script> or <script setup> element
    script_node = None
    is_setup = False
    for child in tree.root_node.children:
        if child.type == "script_element":
            script_node = child
            # Detect <script setup>
            start_tag = next((c for c in child.children if c.type == "start_tag"), None)
            if start_tag:
                tag_text = source_bytes[start_tag.start_byte:start_tag.end_byte].decode("utf-8", errors="replace")
                is_setup = "setup" in tag_text
            break

    if script_node is None:
        return []

    # Detect script language (default: javascript)
    lang = "javascript"
    start_tag = next((c for c in script_node.children if c.type == "start_tag"), None)
    if start_tag:
        for attr in start_tag.children:
            if attr.type == "attribute":
                attr_text = source_bytes[attr.start_byte:attr.end_byte].decode("utf-8", errors="replace")
                if 'lang="ts"' in attr_text or "lang='ts'" in attr_text:
                    lang = "typescript"
                    break
                if 'lang="tsx"' in attr_text or "lang='tsx'" in attr_text:
                    lang = "tsx"
                    break

    # Extract raw_text and its byte/line offset within the .vue file
    raw_node = next((c for c in script_node.children if c.type == "raw_text"), None)
    if raw_node is None:
        return []

    script_bytes = source_bytes[raw_node.start_byte:raw_node.end_byte]
    line_offset = raw_node.start_point[0]  # rows are 0-based

    # Component name from filename (Vue convention: filename = component name)
    component_name = _Path(filename).stem
    symbols: list[Symbol] = []

    # Synthetic component symbol (kind=class, line=1)
    comp_sym = Symbol(
        id=make_symbol_id(filename, component_name, "class"),
        name=component_name,
        qualified_name=component_name,
        kind="class",
        language="vue",
        file=filename,
        line=1,
        end_line=source_bytes.count(b"\n") + 1,
        signature=f"component {component_name}",
        docstring="",
        summary="",
    )
    symbols.append(comp_sym)

    # Re-parse script content with the JS/TS parser
    sub_parser = _get_parser(lang if lang != "tsx" else "typescript")
    sub_tree = sub_parser.parse(script_bytes)

    # Vue Composition API reactive primitives and macros
    _VUE_REACTIVE = frozenset({
        "ref", "reactive", "computed", "watch", "watchEffect",
        "readonly", "shallowRef", "shallowReactive", "toRef", "toRefs",
        "defineProps", "defineEmits", "defineExpose", "defineModel",
        "useRoute", "useRouter", "useStore",
    })

    def _node_text(n) -> str:
        return script_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")

    def _preceding_comment(n) -> str:
        """Return preceding // or /* */ comment text as docstring."""
        # Walk backwards in parent's children list
        parent = n.parent
        if parent is None:
            return ""
        prev = None
        for c in parent.children:
            if c.id == n.id:
                break
            if c.type in ("comment", "template_substitution"):
                prev = c
            elif c.type not in (",", "\n", " "):
                prev = None
        if prev and prev.type == "comment":
            txt = _node_text(prev).strip()
            return txt.lstrip("/").lstrip("*").strip()
        return ""

    def _adjusted_line(n) -> int:
        return n.start_point[0] + line_offset + 1  # 1-based

    def _adjusted_end_line(n) -> int:
        return n.end_point[0] + line_offset + 1

    def _is_vue_reactive_call(node) -> bool:
        """Return True if node is a call_expression to a Vue reactive function."""
        if node.type not in ("call_expression", "await_expression"):
            return False
        func = node.child_by_field_name("function") or (node.children[0] if node.children else None)
        if func is None:
            return False
        name = _node_text(func).split("(")[0].split("<")[0]
        return name in _VUE_REACTIVE

    def _walk_composition(node, parent_id: Optional[str] = None):
        """Walk script AST for Composition API symbols."""
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node)
                sym = Symbol(
                    id=make_symbol_id(filename, name, "class"),
                    name=name,
                    qualified_name=name,
                    kind="class",
                    language="vue",
                    file=filename,
                    line=_adjusted_line(node),
                    end_line=_adjusted_end_line(node),
                    signature=f"class {name}",
                    docstring=_preceding_comment(node),
                    summary="",
                    parent=comp_sym.id,
                )
                symbols.append(sym)
            return  # don't recurse into class body

        elif node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node)
                params = node.child_by_field_name("parameters")
                ret = node.child_by_field_name("return_type")
                sig = f"function {name}{_node_text(params) if params else '()'}"
                if ret:
                    sig += _node_text(ret)
                sym = Symbol(
                    id=make_symbol_id(filename, name, "function"),
                    name=name,
                    qualified_name=f"{component_name}.{name}",
                    kind="function",
                    language="vue",
                    file=filename,
                    line=_adjusted_line(node),
                    end_line=_adjusted_end_line(node),
                    signature=sig,
                    docstring=_preceding_comment(node),
                    summary="",
                    parent=comp_sym.id,
                )
                symbols.append(sym)

        elif node.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            # TypeScript type-level declarations
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node)
                sym = Symbol(
                    id=make_symbol_id(filename, name, "type"),
                    name=name,
                    qualified_name=name,
                    kind="type",
                    language="vue",
                    file=filename,
                    line=_adjusted_line(node),
                    end_line=_adjusted_end_line(node),
                    signature=_node_text(node).split("{")[0].strip(),
                    docstring=_preceding_comment(node),
                    summary="",
                    parent=comp_sym.id,
                )
                symbols.append(sym)
            return

        elif node.type in ("lexical_declaration", "variable_declaration"):
            # const/let declarations — capture Vue reactive + macro calls
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                name_node = decl.child_by_field_name("name")
                val_node = decl.child_by_field_name("value")
                if name_node is None:
                    continue
                name = _node_text(name_node)
                if not name.isidentifier():
                    continue
                # Only capture if RHS is a Vue reactive/macro call
                if val_node and _is_vue_reactive_call(val_node):
                    sig = _node_text(node).split("\n")[0].rstrip("{").strip()
                    sym = Symbol(
                        id=make_symbol_id(filename, name, "constant"),
                        name=name,
                        qualified_name=f"{component_name}.{name}",
                        kind="constant",
                        language="vue",
                        file=filename,
                        line=_adjusted_line(decl),
                        end_line=_adjusted_end_line(decl),
                        signature=sig,
                        docstring=_preceding_comment(node),
                        summary="",
                        parent=comp_sym.id,
                    )
                    symbols.append(sym)

        # Recurse (but not into function bodies to avoid inner helpers)
        skip_recurse = node.type in ("function_declaration", "arrow_function", "function")
        if not skip_recurse:
            for child in node.children:
                _walk_composition(child, parent_id)

    def _walk_options(node):
        """Walk script AST for Options API export default { ... }."""
        # Find: export_statement > object (the options object)
        if node.type == "export_statement":
            for c in node.children:
                if c.type in ("object", "call_expression"):
                    _extract_options_object(c)
            return
        for child in node.children:
            _walk_options(child)

    def _extract_options_object(obj_node):
        """Extract methods/computed/props/data from Options API object."""
        for pair in obj_node.children:
            if pair.type != "pair":
                continue
            key_node = pair.child_by_field_name("key")
            val_node = pair.child_by_field_name("value")
            if key_node is None or val_node is None:
                continue
            key = _node_text(key_node).strip("\"'")

            if key in ("methods", "computed") and val_node.type == "object":
                for method_pair in val_node.children:
                    if method_pair.type in ("pair", "method_definition"):
                        mkey = method_pair.child_by_field_name("key") or method_pair.child_by_field_name("name")
                        if mkey:
                            mname = _node_text(mkey).strip("\"'")
                            sym = Symbol(
                                id=make_symbol_id(filename, mname, "method"),
                                name=mname,
                                qualified_name=f"{component_name}.{mname}",
                                kind="method",
                                language="vue",
                                file=filename,
                                line=_adjusted_line(method_pair),
                                end_line=_adjusted_end_line(method_pair),
                                signature=f"{key}.{mname}()",
                                docstring=_preceding_comment(method_pair),
                                summary="",
                                parent=comp_sym.id,
                            )
                            symbols.append(sym)

            elif key == "props":
                sym = Symbol(
                    id=make_symbol_id(filename, "props", "constant"),
                    name="props",
                    qualified_name=f"{component_name}.props",
                    kind="constant",
                    language="vue",
                    file=filename,
                    line=_adjusted_line(pair),
                    end_line=_adjusted_end_line(pair),
                    signature=f"props: {_node_text(val_node)[:60]}",
                    docstring="",
                    summary="",
                    parent=comp_sym.id,
                )
                symbols.append(sym)

            elif key == "data" and val_node.type in ("function", "arrow_function"):
                sym = Symbol(
                    id=make_symbol_id(filename, "data", "function"),
                    name="data",
                    qualified_name=f"{component_name}.data",
                    kind="function",
                    language="vue",
                    file=filename,
                    line=_adjusted_line(pair),
                    end_line=_adjusted_end_line(pair),
                    signature="data()",
                    docstring=_preceding_comment(pair),
                    summary="",
                    parent=comp_sym.id,
                )
                symbols.append(sym)

    # Dispatch to appropriate extractor
    if is_setup:
        _walk_composition(sub_tree.root_node)
    else:
        # Options API or plain script — try options first, fallback to composition walk
        _walk_options(sub_tree.root_node)
        if len(symbols) == 1:  # only component sym found → try composition
            _walk_composition(sub_tree.root_node)

    return symbols


# ---------------------------------------------------------------------------
# EJS (Embedded JavaScript Templates) custom symbol extractor
# ---------------------------------------------------------------------------

import re as _re

# Matches JS function declarations inside <% %> scriptlet blocks
_EJS_SCRIPTLET_RE = _re.compile(r"<%[-_]?(.*?)[-_]?%>", _re.DOTALL)
_EJS_FUNC_RE = _re.compile(
    r"(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", _re.MULTILINE
)
_EJS_INCLUDE_RE = _re.compile(
    r"""<%[-_]?\s*include\s*\(\s*['"]([^'"]+)['"]\s*[,)]""", _re.MULTILINE
)


def _parse_ejs_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from EJS (Embedded JavaScript Template) files.

    Since no tree-sitter grammar exists for EJS, extraction uses regex:
    - One synthetic "template" symbol per file (guarantees text-search indexing)
    - JS function definitions found inside <% %> scriptlet blocks
    - <%- include('partial') %> calls as import symbols

    Line numbers are 1-based and match positions in the .ejs file.
    """
    content = source_bytes.decode("utf-8", errors="replace")
    lines = content.splitlines()

    # Build a byte-offset → line-number lookup
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line.encode("utf-8")) + 1  # +1 for \n

    def offset_to_line(byte_pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= byte_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    import os as _os
    template_name = _os.path.splitext(_os.path.basename(filename))[0]
    symbols: list[Symbol] = []

    # Synthetic template symbol — ensures the file is stored for text search
    sym_bytes = source_bytes
    symbols.append(Symbol(
        id=make_symbol_id(filename, template_name, "template"),
        file=filename,
        name=template_name,
        qualified_name=template_name,
        kind="template",
        language="ejs",
        signature=f"template {template_name}",
        docstring="",
        parent=None,
        line=1,
        end_line=len(lines),
        byte_offset=0,
        byte_length=len(sym_bytes),
        content_hash=compute_content_hash(sym_bytes),
    ))

    # Extract JS functions from scriptlet blocks
    for scriptlet_match in _EJS_SCRIPTLET_RE.finditer(content):
        scriptlet_text = scriptlet_match.group(1)
        scriptlet_start = scriptlet_match.start()
        for func_match in _EJS_FUNC_RE.finditer(scriptlet_text):
            name = func_match.group(1)
            params = func_match.group(2).strip()
            byte_pos = scriptlet_start + func_match.start()
            line_no = offset_to_line(byte_pos)
            sig = f"function {name}({params})"
            chunk = sig.encode("utf-8")
            symbols.append(Symbol(
                id=make_symbol_id(filename, name, "function"),
                file=filename,
                name=name,
                qualified_name=name,
                kind="function",
                language="ejs",
                signature=sig,
                docstring="",
                parent=None,
                line=line_no,
                end_line=line_no,
                byte_offset=byte_pos,
                byte_length=len(chunk),
                content_hash=compute_content_hash(chunk),
            ))

    # Extract include references as import symbols
    seen_includes: set[str] = set()
    for inc_match in _EJS_INCLUDE_RE.finditer(content):
        partial = inc_match.group(1)
        if partial in seen_includes:
            continue
        seen_includes.add(partial)
        line_no = offset_to_line(inc_match.start())
        sig = f"include('{partial}')"
        chunk = sig.encode("utf-8")
        symbols.append(Symbol(
            id=make_symbol_id(filename, partial, "import"),
            file=filename,
            name=partial,
            qualified_name=partial,
            kind="import",
            language="ejs",
            signature=sig,
            docstring="",
            parent=None,
            line=line_no,
            end_line=line_no,
            byte_offset=inc_match.start(),
            byte_length=len(chunk),
            content_hash=compute_content_hash(chunk),
        ))

    return symbols


def _parse_lua_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Lua source files using tree-sitter.

    Lua uses a single ``function_declaration`` node for all named functions:
    - ``local function name(...)`` — local function, identifier child
    - ``function Module.name(...)`` — module function, dot_index_expression child
    - ``function Module:name(...)`` — OOP method, method_index_expression child

    Name resolution:
    - ``identifier``             → name as-is; kind = "function"
    - ``dot_index_expression``   → "Table.method"; kind = "method"
    - ``method_index_expression``→ "Table:method"; kind = "method"

    Preceding ``--`` line-comments are collected as docstrings.
    """
    from tree_sitter_language_pack import get_parser as _get_parser
    parser = _get_parser("lua")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _resolve_name(name_node) -> tuple[str, str, Optional[str]]:
        """Return (name, qualified_name, parent) for a function name node."""
        ntype = name_node.type
        if ntype == "identifier":
            name = _node_text(name_node)
            return name, name, None
        elif ntype == "dot_index_expression":
            table_node = name_node.child_by_field_name("table")
            field_node = name_node.child_by_field_name("field")
            table = _node_text(table_node) if table_node else ""
            field = _node_text(field_node) if field_node else _node_text(name_node)
            return field, f"{table}.{field}", table or None
        elif ntype == "method_index_expression":
            table_node = name_node.child_by_field_name("table")
            method_node = name_node.child_by_field_name("method")
            table = _node_text(table_node) if table_node else ""
            method = _node_text(method_node) if method_node else _node_text(name_node)
            return method, f"{table}:{method}", table or None
        else:
            text = _node_text(name_node)
            return text, text, None

    def _collect_docstring(node) -> str:
        """Collect preceding -- comment siblings as a docstring."""
        comments: list[str] = []
        prev = node.prev_named_sibling
        while prev and prev.type == "comment":
            raw = _node_text(prev)
            line = raw.lstrip("-").strip()
            comments.insert(0, line)
            prev = prev.prev_named_sibling
        return "\n".join(comments) if comments else ""

    def _walk(node) -> None:
        if node.type == "function_declaration":
            _extract_lua_function(node)
        for child in node.children:
            _walk(child)

    def _extract_lua_function(node) -> None:
        name_node = None
        params_node = None
        is_local = False

        for child in node.children:
            if child.type == "local":
                is_local = True
            elif child.type in ("identifier", "dot_index_expression", "method_index_expression"):
                name_node = child
            elif child.type == "parameters":
                params_node = child

        if name_node is None:
            return

        name, qualified_name, parent = _resolve_name(name_node)
        if not name:
            return

        kind = "method" if name_node.type in ("dot_index_expression", "method_index_expression") else "function"
        params_text = _node_text(params_node) if params_node else "()"
        prefix = "local function" if is_local else "function"
        signature = f"{prefix} {qualified_name}{params_text}"
        docstring = _collect_docstring(node)

        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, qualified_name, kind),
            file=filename,
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            language="lua",
            signature=signature,
            docstring=docstring,
            parent=parent,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    _walk(tree.root_node)
    symbols.sort(key=lambda s: s.line)
    return symbols


def _parse_luau_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Luau (Roblox) source files using tree-sitter.

    Luau is Roblox's typed superset of Lua.  Function declarations use the
    same ``function_declaration`` node type as Lua, with ``name``,
    ``parameters``, and ``body`` named fields:

    - ``local function name(p: T): R`` — local function, ``identifier`` name child
    - ``function Module.name(p: T): R`` — module function, ``dot_index_expression``
    - ``function Module:name(p: T): R`` — OOP method, ``method_index_expression``

    Additionally, Luau supports:
    - ``type_definition`` — ``type Foo = ...`` and ``export type Foo = ...``
      with an ``identifier`` name child
    - Typed parameters and return type annotations (captured in signature text)

    Preceding ``--`` line-comments are collected as docstrings.
    """
    from tree_sitter_language_pack import get_parser as _get_parser
    parser = _get_parser("luau")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _resolve_name(name_node) -> tuple[str, str, Optional[str]]:
        """Return (name, qualified_name, parent) for a function name node."""
        ntype = name_node.type
        if ntype == "identifier":
            name = _node_text(name_node)
            return name, name, None
        elif ntype == "dot_index_expression":
            table_node = name_node.child_by_field_name("table")
            field_node = name_node.child_by_field_name("field")
            table = _node_text(table_node) if table_node else ""
            field = _node_text(field_node) if field_node else _node_text(name_node)
            return field, f"{table}.{field}", table or None
        elif ntype == "method_index_expression":
            table_node = name_node.child_by_field_name("table")
            method_node = name_node.child_by_field_name("method")
            table = _node_text(table_node) if table_node else ""
            method = _node_text(method_node) if method_node else _node_text(name_node)
            return method, f"{table}:{method}", table or None
        else:
            text = _node_text(name_node)
            return text, text, None

    def _collect_docstring(node) -> str:
        """Collect preceding -- comment siblings as a docstring."""
        comments: list[str] = []
        prev = node.prev_named_sibling
        while prev and prev.type == "comment":
            raw = _node_text(prev)
            line = raw.lstrip("-").strip()
            comments.insert(0, line)
            prev = prev.prev_named_sibling
        return "\n".join(comments) if comments else ""

    def _walk(node) -> None:
        if node.type == "function_declaration":
            _extract_luau_function(node)
        elif node.type == "type_definition":
            _extract_luau_type(node)
        for child in node.children:
            _walk(child)

    def _extract_luau_function(node) -> None:
        name_node = None
        params_node = None
        is_local = False

        for child in node.children:
            if child.type == "local":
                is_local = True
            elif child.type in ("identifier", "dot_index_expression", "method_index_expression") and name_node is None:
                name_node = child
            elif child.type == "parameters":
                params_node = child

        if name_node is None:
            return

        name, qualified_name, parent = _resolve_name(name_node)
        if not name:
            return

        kind = "method" if name_node.type in ("dot_index_expression", "method_index_expression") else "function"
        params_text = _node_text(params_node) if params_node else "()"
        prefix = "local function" if is_local else "function"

        # Capture return type annotation if present (between params ')' and 'block').
        # The AST places a ':' token, then a type node (identifier, builtin_type,
        # object_type, union_type, etc.) between the parameters and the block.
        # Skip comment nodes that may appear in the same region.
        return_type = ""
        seen_params = False
        seen_colon = False
        for child in node.children:
            if child.type == "parameters":
                seen_params = True
                seen_colon = False
            elif seen_params and child.type == ":":
                seen_colon = True
            elif seen_params and child.type in ("block", "end"):
                break
            elif seen_params and child.type == "comment":
                continue
            elif seen_params and seen_colon:
                return_type = _node_text(child)
                break

        signature = f"{prefix} {qualified_name}{params_text}"
        if return_type:
            signature += f": {return_type}"
        docstring = _collect_docstring(node)

        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, qualified_name, kind),
            file=filename,
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            language="luau",
            signature=signature,
            docstring=docstring,
            parent=parent,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    def _extract_luau_type(node) -> None:
        """Extract ``type Foo = ...`` and ``export type Foo = ...`` definitions."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = _node_text(name_node)
        if not name:
            return

        is_export = any(child.type == "export" for child in node.children)
        prefix = "export type" if is_export else "type"

        # Build a compact signature from the full node text (first line only for brevity)
        full_text = _node_text(node)
        first_line = full_text.split("\n", 1)[0].rstrip()
        signature = first_line

        docstring = _collect_docstring(node)

        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, name, "type"),
            file=filename,
            name=name,
            qualified_name=name,
            kind="type",
            language="luau",
            signature=signature,
            docstring=docstring,
            parent=None,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    _walk(tree.root_node)
    symbols.sort(key=lambda s: s.line)
    return symbols


def _parse_erlang_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Erlang source files using tree-sitter.

    Erlang's grammar surfaces the following top-level forms in source_file:

    - ``fun_decl``   — one node per *clause* (multi-clause functions produce
                       multiple nodes).  Name = first ``atom`` in the first
                       ``function_clause``.  Arity = named-child count of
                       ``expr_args``.  Only the first clause for a given
                       (name, arity) pair is emitted; subsequent clauses are
                       merged by incrementing the end-line to cover the whole
                       function body.
    - ``type_alias`` / ``opaque`` — type definitions.  Name from
                       ``type_name → atom``.
    - ``record_decl``— record (struct-like) declarations.  Name from first
                       ``atom`` named child.
    - ``pp_define``  — macro constants.  Name from ``macro_lhs → var/atom``.

    Docstrings are collected from preceding ``comment`` siblings (``%% …``).
    """
    from tree_sitter_language_pack import get_parser as _get_parser

    parser = _get_parser("erlang")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []
    # Track (name, arity) to deduplicate multi-clause fun_decls.
    # Maps (name, arity) -> index into symbols list for end_line update.
    seen_funs: dict[tuple[str, int], int] = {}

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _collect_docstring(node) -> str:
        """Collect preceding %% comment siblings as a docstring."""
        lines: list[str] = []
        prev = node.prev_named_sibling
        while prev and prev.type == "comment":
            raw = _node_text(prev).lstrip("%").strip()
            # Strip @doc / @spec tags (EDoc convention)
            if raw.startswith("@doc"):
                raw = raw[4:].strip()
            lines.insert(0, raw)
            prev = prev.prev_named_sibling
        return "\n".join(lines) if lines else ""

    def _extract_fun_decl(node) -> None:
        # Get the first function_clause named child
        clause = None
        for child in node.named_children:
            if child.type == "function_clause":
                clause = child
                break
        if clause is None:
            return

        # Name = first atom named child of clause
        name_node = None
        args_node = None
        for child in clause.named_children:
            if child.type == "atom" and name_node is None:
                name_node = child
            elif child.type == "expr_args" and args_node is None:
                args_node = child

        if name_node is None:
            return

        name = _node_text(name_node)
        arity = len(args_node.named_children) if args_node else 0
        args_text = _node_text(args_node) if args_node else "()"

        key = (name, arity)
        if key in seen_funs:
            # Update end_line of the existing symbol to cover this clause
            idx = seen_funs[key]
            end_row, _ = node.end_point
            existing = symbols[idx]
            symbols[idx] = Symbol(
                id=existing.id,
                file=existing.file,
                name=existing.name,
                qualified_name=existing.qualified_name,
                kind=existing.kind,
                language=existing.language,
                signature=existing.signature,
                docstring=existing.docstring,
                parent=existing.parent,
                line=existing.line,
                end_line=end_row + 1,
                byte_offset=existing.byte_offset,
                byte_length=(node.end_byte - existing.byte_offset),
                content_hash=existing.content_hash,
            )
            return

        signature = f"{name}{args_text}"
        docstring = _collect_docstring(node)
        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        idx = len(symbols)
        seen_funs[key] = idx
        symbols.append(Symbol(
            id=make_symbol_id(filename, f"{name}/{arity}", "function"),
            file=filename,
            name=name,
            qualified_name=f"{name}/{arity}",
            kind="function",
            language="erlang",
            signature=signature,
            docstring=docstring,
            parent=None,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    def _extract_type(node) -> None:
        """Handle type_alias and opaque nodes."""
        type_name_node = None
        for child in node.named_children:
            if child.type == "type_name":
                type_name_node = child
                break
        if type_name_node is None:
            return

        atom_node = None
        for child in type_name_node.named_children:
            if child.type == "atom":
                atom_node = child
                break
        if atom_node is None:
            return

        name = _node_text(atom_node)
        type_sig = _node_text(type_name_node)
        docstring = _collect_docstring(node)
        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, name, "type"),
            file=filename,
            name=name,
            qualified_name=name,
            kind="type",
            language="erlang",
            signature=f"-type {type_sig}",
            docstring=docstring,
            parent=None,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    def _extract_record(node) -> None:
        """Handle record_decl nodes (struct-like)."""
        atom_node = None
        for child in node.named_children:
            if child.type == "atom":
                atom_node = child
                break
        if atom_node is None:
            return

        name = _node_text(atom_node)
        docstring = _collect_docstring(node)
        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, name, "type"),
            file=filename,
            name=name,
            qualified_name=name,
            kind="type",
            language="erlang",
            signature=f"-record({name}, ...)",
            docstring=docstring,
            parent=None,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    def _extract_define(node) -> None:
        """Handle pp_define (macro constant) nodes."""
        macro_lhs = None
        for child in node.named_children:
            if child.type == "macro_lhs":
                macro_lhs = child
                break
        if macro_lhs is None:
            return

        # macro_lhs contains a var or atom for the macro name
        name_node = None
        for child in macro_lhs.named_children:
            if child.type in ("var", "atom"):
                name_node = child
                break
        if name_node is None:
            return

        name = _node_text(name_node)
        full_text = _node_text(node)
        # Trim trailing '.' for a cleaner signature
        signature = full_text.rstrip(".")
        docstring = _collect_docstring(node)
        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]

        symbols.append(Symbol(
            id=make_symbol_id(filename, name, "constant"),
            file=filename,
            name=name,
            qualified_name=name,
            kind="constant",
            language="erlang",
            signature=signature,
            docstring=docstring,
            parent=None,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    for node in tree.root_node.named_children:
        if node.type == "fun_decl":
            _extract_fun_decl(node)
        elif node.type in ("type_alias", "opaque"):
            _extract_type(node)
        elif node.type == "record_decl":
            _extract_record(node)
        elif node.type == "pp_define":
            _extract_define(node)

    symbols.sort(key=lambda s: s.line)
    return symbols


def _parse_fortran_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from Fortran source files using tree-sitter.

    Handles free-form and fixed-form Fortran (F77–F2018).  The grammar's
    ``translation_unit`` root contains:

    - ``function`` / ``subroutine`` — top-level procedures.  Name from the
      inner ``function_statement`` / ``subroutine_statement`` → ``name`` field.
    - ``module`` — namespace/container.  Extracted as kind ``"class"``.
      Procedures inside ``internal_procedures`` are extracted as kind
      ``"method"`` with the module name as parent.  ``derived_type_definition``
      nodes inside the module become ``"type"`` symbols.  ``variable_declaration``
      nodes with a ``parameter`` qualifier become ``"constant"`` symbols.
    - ``program`` — top-level program block.  Extracted as kind ``"class"``
      so it appears in outlines; its ``contains`` procedures are extracted
      as ``"method"`` symbols.

    Preceding ``!`` comments are collected as docstrings.
    """
    from tree_sitter_language_pack import get_parser as _get_parser

    parser = _get_parser("fortran")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _collect_docstring(node) -> str:
        """Collect preceding ! comment siblings as a docstring."""
        lines: list[str] = []
        prev = node.prev_named_sibling
        while prev and prev.type == "comment":
            raw = _node_text(prev).lstrip("!").strip()
            lines.insert(0, raw)
            prev = prev.prev_named_sibling
        return "\n".join(lines) if lines else ""

    def _make_sym(
        node,
        name: str,
        qualified_name: str,
        kind: str,
        signature: str,
        docstring: str,
        parent: Optional[str],
    ) -> None:
        row, _ = node.start_point
        end_row, _ = node.end_point
        sym_bytes = source_bytes[node.start_byte:node.end_byte]
        symbols.append(Symbol(
            id=make_symbol_id(filename, qualified_name, kind),
            file=filename,
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            language="fortran",
            signature=signature,
            docstring=docstring,
            parent=parent,
            line=row + 1,
            end_line=end_row + 1,
            byte_offset=node.start_byte,
            byte_length=len(sym_bytes),
            content_hash=compute_content_hash(sym_bytes),
        ))

    def _extract_procedure(node, parent_name: Optional[str] = None) -> None:
        """Extract a function or subroutine node."""
        stmt_type = "function_statement" if node.type == "function" else "subroutine_statement"
        stmt = next((c for c in node.named_children if c.type == stmt_type), None)
        if stmt is None:
            return

        name_node = stmt.child_by_field_name("name")
        params_node = stmt.child_by_field_name("parameters")
        if name_node is None:
            return

        name = _node_text(name_node)
        params = _node_text(params_node) if params_node else "()"
        kind = "method" if parent_name else "function"
        qualified_name = f"{parent_name}::{name}" if parent_name else name
        keyword = "function" if node.type == "function" else "subroutine"
        signature = f"{keyword} {name}{params}"
        docstring = _collect_docstring(node)

        _make_sym(node, name, qualified_name, kind, signature, docstring, parent_name)

    def _extract_derived_type(node, parent_name: Optional[str] = None) -> None:
        """Extract a derived_type_definition node."""
        stmt = next((c for c in node.named_children if c.type == "derived_type_statement"), None)
        if stmt is None:
            return

        # Name is in a type_name child of the statement
        type_name_node = next(
            (c for c in stmt.named_children if c.type == "type_name"),
            None,
        )
        if type_name_node is None:
            return

        name = _node_text(type_name_node).strip()
        qualified_name = f"{parent_name}::{name}" if parent_name else name
        signature = f"type :: {name}"
        docstring = _collect_docstring(node)

        _make_sym(node, name, qualified_name, "type", signature, docstring, parent_name)

    def _is_parameter_decl(node) -> bool:
        """Return True if a variable_declaration has a 'parameter' qualifier."""
        return any(
            c.type == "type_qualifier" and _node_text(c).strip().lower() == "parameter"
            for c in node.named_children
        )

    def _extract_parameter_constants(node, parent_name: Optional[str] = None) -> None:
        """Extract named constants from a variable_declaration with parameter qualifier."""
        for child in node.named_children:
            if child.type == "init_declarator":
                id_node = child.child_by_field_name("name")
                if id_node is None:
                    # Fallback: first identifier named child
                    id_node = next(
                        (c for c in child.named_children if c.type == "identifier"),
                        None,
                    )
                if id_node is None:
                    continue
                name = _node_text(id_node).strip()
                qualified_name = f"{parent_name}::{name}" if parent_name else name
                signature = _node_text(node).strip()
                docstring = _collect_docstring(node)
                _make_sym(node, name, qualified_name, "constant", signature, docstring, parent_name)

    def _walk_scope(nodes, parent_name: Optional[str] = None) -> None:
        """Walk a sequence of nodes extracting symbols with an optional parent."""
        for node in nodes:
            if node.type in ("function", "subroutine"):
                _extract_procedure(node, parent_name)
            elif node.type == "derived_type_definition":
                _extract_derived_type(node, parent_name)
            elif node.type == "variable_declaration" and _is_parameter_decl(node):
                _extract_parameter_constants(node, parent_name)
            elif node.type == "internal_procedures":
                _walk_scope(node.named_children, parent_name)

    def _extract_module_or_program(node) -> None:
        """Extract a module or program block as a class-like container."""
        stmt_type = "module_statement" if node.type == "module" else "program_statement"
        stmt = next((c for c in node.named_children if c.type == stmt_type), None)
        if stmt is None:
            # Still recurse to catch nested procedures
            _walk_scope(node.named_children)
            return

        name_node = stmt.child_by_field_name("name") or next(
            (c for c in stmt.named_children if c.type == "name"), None
        )
        if name_node is None:
            _walk_scope(node.named_children)
            return

        name = _node_text(name_node).strip()
        keyword = "module" if node.type == "module" else "program"
        signature = f"{keyword} {name}"
        docstring = _collect_docstring(node)
        _make_sym(node, name, name, "class", signature, docstring, None)

        # Recurse into the module/program body with this name as parent
        _walk_scope(node.named_children, parent_name=name)

    # Walk translation_unit top-level children
    for node in tree.root_node.named_children:
        if node.type in ("function", "subroutine"):
            _extract_procedure(node, parent_name=None)
        elif node.type in ("module", "program"):
            _extract_module_or_program(node)
        elif node.type == "derived_type_definition":
            _extract_derived_type(node)
        elif node.type == "variable_declaration" and _is_parameter_decl(node):
            _extract_parameter_constants(node)

    symbols.sort(key=lambda s: s.line)
    return symbols


def _parse_sql_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from SQL source files using tree-sitter.

    The derekstride/tree-sitter-sql grammar exposes these top-level node types
    (inside ``program → statement``):

    - ``create_table``    — table DDL.  Name in ``object_reference → identifier``.
    - ``create_view``     — view DDL.   Name in ``object_reference → identifier``.
    - ``create_function`` — UDF/stored function.  Name in ``object_reference``.
                            Parameters in ``function_arguments``.
    - ``create_index``    — index DDL.  Name is a direct ``identifier`` child.
    - ``create_schema``   — schema DDL. Name is a direct ``identifier`` child.
    - ``cte``             — CTE definition inside a WITH clause.  Name is a
                            direct ``identifier`` child.

    ``CREATE PROCEDURE`` and ``CREATE TRIGGER`` produce ERROR nodes in this
    grammar and are not extracted.

    Jinja-templated SQL (dbt models) is pre-processed by ``sql_preprocessor``
    to replace ``{{ }}``, ``{% %}``, and ``{# #}`` tokens with ``__jinja__``
    before parsing.  dbt directives (``{% macro %}``, ``{% test %}``,
    ``{% snapshot %}``, ``{% materialization %}``) are extracted as symbols
    before stripping.
    """
    from tree_sitter_language_pack import get_parser as _get_parser
    from .sql_preprocessor import strip_jinja, is_jinja_sql, extract_dbt_directives

    # Extract dbt directives before stripping Jinja (macro, test, snapshot, etc.)
    dbt_symbols: list[Symbol] = []
    has_jinja = is_jinja_sql(source_bytes)
    if has_jinja:
        dbt_directives = extract_dbt_directives(source_bytes)
        for d in dbt_directives:
            # Map directive type to symbol kind
            if d.directive in ("macro", "test", "materialization"):
                kind = "function"
            else:  # snapshot
                kind = "type"

            # Build a readable signature
            if d.params:
                sig = f"{{% {d.directive} {d.name}({d.params}) %}}"
            else:
                sig = f"{{% {d.directive} {d.name} %}}"

            c_hash = compute_content_hash(
                source_bytes[d.byte_offset:d.byte_offset + d.byte_length]
            )

            dbt_symbols.append(Symbol(
                id=make_symbol_id(filename, d.name, kind),
                file=filename,
                name=d.name,
                qualified_name=d.name,
                kind=kind,
                language="sql",
                signature=sig,
                docstring=d.docstring,
                line=d.line,
                end_line=d.end_line,
                byte_offset=d.byte_offset,
                byte_length=d.byte_length,
                content_hash=c_hash,
            ))

        source_bytes = strip_jinja(source_bytes)

    try:
        parser = _get_parser("sql")
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    symbols: list[Symbol] = []

    # Node types we extract and their symbol kind
    NODE_KIND_MAP = {
        "create_table": "type",
        "create_view": "type",
        "create_function": "function",
        "create_index": "type",
        "create_schema": "type",
        "cte": "function",
    }

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_name(node) -> str | None:
        """Extract the name from a SQL DDL node."""
        node_type = node.type

        # create_table, create_view, create_function: name in object_reference child
        if node_type in ("create_table", "create_view", "create_function"):
            for child in node.children:
                if child.type == "object_reference":
                    # object_reference may contain schema.name (multiple identifiers)
                    # Take the full text as the name (e.g. "schema.table_name")
                    return _node_text(child)
            return None

        # create_index, create_schema, cte: name is a direct identifier child
        if node_type in ("create_index", "create_schema", "cte"):
            for child in node.children:
                if child.type == "identifier":
                    return _node_text(child)
            return None

        return None

    def _build_signature(node) -> str:
        """Build a concise signature for a SQL symbol."""
        node_type = node.type

        if node_type == "create_function":
            name = _extract_name(node) or "?"
            # Look for function_arguments and return type
            args_text = ""
            return_text = ""
            for child in node.children:
                if child.type == "function_arguments":
                    args_text = _node_text(child)
                elif child.type == "keyword_returns":
                    # Return type is the next sibling after RETURNS keyword
                    idx = node.children.index(child)
                    if idx + 1 < len(node.children):
                        return_text = f" RETURNS {_node_text(node.children[idx + 1])}"
            return f"CREATE FUNCTION {name}{args_text}{return_text}"

        if node_type == "create_table":
            name = _extract_name(node) or "?"
            # Include column list summary
            for child in node.children:
                if child.type == "column_definitions":
                    cols = [_node_text(c).split()[0] for c in child.children
                            if c.type == "column_definition"]
                    if cols:
                        return f"CREATE TABLE {name} ({', '.join(cols)})"
            return f"CREATE TABLE {name}"

        if node_type == "create_view":
            name = _extract_name(node) or "?"
            return f"CREATE VIEW {name}"

        if node_type == "create_index":
            name = _extract_name(node) or "?"
            # Find the ON target
            on_target = ""
            for i, child in enumerate(node.children):
                if child.type == "keyword_on" and i + 1 < len(node.children):
                    on_target = f" ON {_node_text(node.children[i + 1])}"
            return f"CREATE INDEX {name}{on_target}"

        if node_type == "create_schema":
            name = _extract_name(node) or "?"
            return f"CREATE SCHEMA {name}"

        if node_type == "cte":
            name = _extract_name(node) or "?"
            return f"WITH {name} AS (...)"

        return _node_text(node)[:120]

    def _collect_docstring(node) -> str:
        """Collect preceding -- or /* */ comment siblings as a docstring."""
        lines: list[str] = []
        prev = node.prev_named_sibling
        while prev and prev.type in ("comment", "marginalia"):
            raw = _node_text(prev).lstrip("-").lstrip("/").lstrip("*").strip()
            lines.insert(0, raw)
            prev = prev.prev_named_sibling
        return "\n".join(lines) if lines else ""

    def _walk(node) -> None:
        """Recursively walk the AST to find extractable nodes."""
        if node.type in NODE_KIND_MAP:
            name = _extract_name(node)
            if name:
                kind = NODE_KIND_MAP[node.type]
                signature = _build_signature(node)

                # Collect docstring from preceding comment sibling
                # For nodes inside statement wrappers, check the statement's sibling
                doc_node = node
                if node.parent and node.parent.type == "statement":
                    doc_node = node.parent
                docstring = _collect_docstring(doc_node)

                c_hash = compute_content_hash(
                    source_bytes[node.start_byte:node.end_byte]
                )

                sym = Symbol(
                    id=make_symbol_id(filename, name, kind),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    language="sql",
                    signature=signature,
                    docstring=docstring,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=c_hash,
                )
                symbols.append(sym)

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)

    # Merge dbt directive symbols with tree-sitter SQL symbols
    all_symbols = dbt_symbols + symbols
    all_symbols.sort(key=lambda s: s.line)
    return all_symbols


def _parse_objc_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse Objective-C source and extract class interfaces, implementations, and methods."""
    try:
        parser = get_parser("objc")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    CLASS_NODE_TYPES = {
        "class_interface": "class",
        "class_implementation": "class",
        "category_interface": "class",
        "category_implementation": "class",
        "protocol_declaration": "type",
    }

    def _get_class_name(node) -> Optional[str]:
        """First identifier child is the class name in ObjC @interface/@implementation."""
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte]
        return None

    def _get_selector(node) -> Optional[str]:
        """Build an ObjC method selector from identifier and method_parameter children.

        Simple method  - (void)bar          -> "bar"
        Multi-keyword  - (void)foo:(id)x    -> "foo:"
        Multi-keyword  - (void)foo:(id)x bar:(id)y -> "foo:bar:"
        """
        identifiers: list[str] = []
        has_params = False
        for child in node.children:
            if child.type == "identifier":
                identifiers.append(source[child.start_byte:child.end_byte])
            elif child.type == "method_parameter":
                has_params = True
        if not identifiers:
            return None
        if has_params:
            return ":".join(identifiers) + ":"
        return identifiers[0]

    current_class: list[Optional[str]] = [None]

    def _walk(node) -> None:
        if node.type in CLASS_NODE_TYPES:
            name = _get_class_name(node)
            if name:
                prev_class = current_class[0]
                current_class[0] = name
                sym = Symbol(
                    id=make_symbol_id(filename, name, CLASS_NODE_TYPES[node.type]),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind=CLASS_NODE_TYPES[node.type],
                    language="objc",
                    signature=f"@{node.type.replace('_', ' ')} {name}",
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)
                for child in node.children:
                    _walk(child)
                current_class[0] = prev_class
                return
        elif node.type in ("method_declaration", "method_definition") and current_class[0]:
            selector = _get_selector(node)
            if selector:
                qualified = f"{current_class[0]}.{selector}"
                raw_sig = source[node.start_byte:node.start_byte + min(120, node.end_byte - node.start_byte)]
                sym = Symbol(
                    id=make_symbol_id(filename, qualified, "method"),
                    file=filename,
                    name=selector,
                    qualified_name=qualified,
                    kind="method",
                    language="objc",
                    signature=raw_sig.split("{")[0].strip(),
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)
                return
        elif node.type == "function_definition":
            name = None
            for child in node.children:
                if child.type == "function_declarator":
                    for sub in child.children:
                        if sub.type == "identifier":
                            name = source[sub.start_byte:sub.end_byte]
                            break
            if name:
                raw_sig = source[node.start_byte:node.start_byte + min(120, node.end_byte - node.start_byte)]
                sym = Symbol(
                    id=make_symbol_id(filename, name, "function"),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind="function",
                    language="objc",
                    signature=raw_sig.split("{")[0].strip(),
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return symbols


def _parse_proto_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse Protocol Buffer source and extract messages, services, RPCs, and enums."""
    try:
        parser = get_parser("proto")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    NODE_MAP = {
        "message": ("class", "message_name"),
        "enum": ("type", "enum_name"),
        "service": ("class", "service_name"),
        "rpc": ("method", "rpc_name"),
        "extend": ("class", "message_name"),
    }

    def _get_name(node, name_child_type: str) -> Optional[str]:
        """Find the name child node and return its text.

        Name nodes (e.g. message_name) contain a single identifier child.
        Return the full text of the name node which equals the identifier text.
        """
        for child in node.children:
            if child.type == name_child_type:
                return source[child.start_byte:child.end_byte].strip()
        return None

    def _walk(node, scope: str = "") -> None:
        if node.type in NODE_MAP:
            kind, name_child_type = NODE_MAP[node.type]
            name = _get_name(node, name_child_type)
            if name:
                qualified = f"{scope}.{name}" if scope else name
                sym = Symbol(
                    id=make_symbol_id(filename, qualified, kind),
                    file=filename,
                    name=name,
                    qualified_name=qualified,
                    kind=kind,
                    language="proto",
                    signature=f"{node.type} {name}",
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)
                new_scope = qualified if node.type in ("message", "service") else scope
                for child in node.children:
                    _walk(child, new_scope)
                return
        for child in node.children:
            _walk(child, scope)

    _walk(tree.root_node)
    return symbols


def _parse_hcl_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse HCL/Terraform source and extract named blocks as symbols.

    resource "aws_instance" "web"  -> name="aws_instance.web", kind=class
    variable "name"                -> name="name",             kind=constant
    module "vpc"                   -> name="vpc",              kind=class
    output "ip"                    -> name="ip",               kind=constant
    provider "aws"                 -> name="aws",              kind=type
    """
    try:
        parser = get_parser("hcl")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    BLOCK_KINDS = {
        "resource": "class",
        "data": "class",
        "module": "class",
        "variable": "constant",
        "output": "constant",
        "locals": "constant",
        "provider": "type",
        "terraform": "type",
    }

    def _string_lit_text(node) -> str:
        """Extract the string value from a string_lit node.

        HCL string_lit children: quoted_template_start + template_literal + quoted_template_end
        """
        for child in node.children:
            if child.type == "template_literal":
                return source[child.start_byte:child.end_byte].strip()
        # fallback: strip surrounding quotes from raw text
        return source[node.start_byte:node.end_byte].strip().strip('"')

    def _walk(node) -> None:
        if node.type == "block":
            block_type: Optional[str] = None
            labels: list[str] = []
            for child in node.children:
                if child.type == "identifier" and block_type is None:
                    block_type = source[child.start_byte:child.end_byte].strip()
                elif child.type == "string_lit" and block_type is not None:
                    label = _string_lit_text(child)
                    if label:
                        labels.append(label)
                elif child.type in ("block_start", "body"):
                    break

            if block_type and block_type in BLOCK_KINDS:
                kind = BLOCK_KINDS[block_type]
                if block_type in ("resource", "data") and len(labels) >= 2:
                    name = f"{labels[0]}.{labels[1]}"
                    signature = f'{block_type} "{labels[0]}" "{labels[1]}"'
                elif labels:
                    name = labels[0]
                    signature = f'{block_type} "{labels[0]}"'
                else:
                    name = block_type
                    signature = block_type

                sym = Symbol(
                    id=make_symbol_id(filename, name, kind),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    language="hcl",
                    signature=signature,
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return symbols


def _parse_graphql_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse GraphQL schema/query files and extract type, operation, and fragment definitions."""
    try:
        parser = get_parser("graphql")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    NODE_KINDS = {
        "object_type_definition": "type",
        "interface_type_definition": "type",
        "union_type_definition": "type",
        "enum_type_definition": "type",
        "input_object_type_definition": "type",
        "scalar_type_definition": "type",
        "schema_definition": "type",
        "object_type_extension": "type",
        "interface_type_extension": "type",
        "enum_type_extension": "type",
        "input_object_type_extension": "type",
        "operation_definition": "function",
        "fragment_definition": "function",
    }

    def _get_name(node) -> Optional[str]:
        for child in node.children:
            if child.type == "name":
                return source[child.start_byte:child.end_byte].strip()
            if child.type == "fragment_name":
                return source[child.start_byte:child.end_byte].strip() or None
        return None

    def _walk(node) -> None:
        if node.type in NODE_KINDS:
            kind = NODE_KINDS[node.type]
            name = _get_name(node)
            if not name and node.type == "operation_definition":
                for child in node.children:
                    if child.type == "operation_type":
                        name = source[child.start_byte:child.end_byte].strip()
                        break
                name = name or "anonymous"
            if not name and node.type == "schema_definition":
                name = "schema"
            if name:
                short = node.type.replace("_definition", "").replace("_extension", "").replace("_type", "")
                sym = Symbol(
                    id=make_symbol_id(filename, name, kind),
                    file=filename,
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    language="graphql",
                    signature=f"{short} {name}",
                    docstring="",
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)
            return  # don't recurse into definitions

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return symbols


def _parse_julia_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse Julia source and extract functions, macros, structs, and modules.

    Julia's tree-sitter grammar nests function names inside a signature node:
      function_definition > signature > call_expression > identifier("name")
    Struct names live in a type_head node:
      struct_definition > type_head > identifier("Name")
    Module names are direct identifier children.
    """
    try:
        parser = get_parser("julia")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    def _func_name(node) -> Optional[str]:
        """Extract name from function_definition via signature > call_expression > identifier."""
        for child in node.children:
            if child.type == "signature":
                for sub in child.children:
                    if sub.type == "call_expression":
                        for inner in sub.children:
                            if inner.type == "identifier":
                                return source[inner.start_byte:inner.end_byte]
                    elif sub.type == "identifier":
                        return source[sub.start_byte:sub.end_byte]
        return None

    def _struct_name(node) -> Optional[str]:
        """Extract name from struct_definition via type_head > identifier."""
        for child in node.children:
            if child.type == "type_head":
                for sub in child.children:
                    if sub.type == "identifier":
                        return source[sub.start_byte:sub.end_byte]
            elif child.type == "identifier":
                return source[child.start_byte:child.end_byte]
        return None

    def _direct_name(node) -> Optional[str]:
        """Return first identifier child text."""
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte]
        return None

    def _walk(node, scope: str = "") -> None:
        name: Optional[str] = None
        kind: Optional[str] = None

        if node.type in ("function_definition", "short_function_definition"):
            name = _func_name(node)
            kind = "function"
        elif node.type == "macro_definition":
            name = _direct_name(node)
            kind = "function"
        elif node.type in ("struct_definition", "mutable_struct_definition"):
            name = _struct_name(node)
            kind = "type"
        elif node.type == "abstract_definition":
            name = _struct_name(node) or _direct_name(node)
            kind = "type"
        elif node.type == "module_definition":
            name = _direct_name(node)
            kind = "class"

        if name and kind:
            qualified = f"{scope}.{name}" if scope else name
            sym = Symbol(
                id=make_symbol_id(filename, qualified, kind),
                file=filename,
                name=name,
                qualified_name=qualified,
                kind=kind,
                language="julia",
                signature=source[node.start_byte:node.start_byte + min(120, node.end_byte - node.start_byte)].split("\n")[0].strip(),
                docstring="",
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                byte_offset=node.start_byte,
                byte_length=node.end_byte - node.start_byte,
                content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
            )
            symbols.append(sym)
            new_scope = qualified if node.type == "module_definition" else scope
            for child in node.children:
                _walk(child, new_scope)
            return

        for child in node.children:
            _walk(child, scope)

    _walk(tree.root_node)
    return symbols


def _parse_groovy_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse Groovy source and extract classes, interfaces, and methods.

    tree-sitter-groovy uses a low-level grammar: all constructs are 'command'
    nodes containing 'unit' (keyword/type/name) and 'block' children.

    Class:     command > unit[identifier("class")] + block[unit[identifier(Name)]]
    Interface: command > unit[identifier("interface")] + block[unit[identifier(Name)]]
    Method:    command > unit[identifier(type)] + block[unit[func[identifier(name), arg_block]]]
    Def func:  command > unit[identifier("def")] + block[unit[func[identifier(name), arg_block]]]
    """
    try:
        parser = get_parser("groovy")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    CONTAINER_KEYWORDS = {"class", "interface", "enum", "trait", "record"}

    def _id_text(node) -> Optional[str]:
        """Return text if node is an identifier, else None."""
        if node.type == "identifier":
            return source[node.start_byte:node.end_byte]
        return None

    def _first_id_in_unit(unit_node) -> Optional[str]:
        """Get first identifier text inside a 'unit' node."""
        for child in unit_node.children:
            t = _id_text(child)
            if t:
                return t
        return None

    def _func_name_in_unit(unit_node) -> Optional[str]:
        """Find a func > identifier name inside a unit node."""
        for child in unit_node.children:
            if child.type == "func":
                for sub in child.children:
                    t = _id_text(sub)
                    if t:
                        return t
        return None

    def _walk_commands(nodes, scope: str = "") -> None:
        """Walk a list of sibling nodes looking for command patterns."""
        for node in nodes:
            if node.type != "command":
                continue

            units = [c for c in node.children if c.type == "unit"]
            block = next((c for c in node.children if c.type == "block"), None)

            if not units:
                continue

            first_kw = _first_id_in_unit(units[0])

            # Class / interface / enum / trait declaration
            if first_kw in CONTAINER_KEYWORDS and block:
                # Name is in second unit, or first unit of block
                class_name: Optional[str] = None
                if len(units) >= 2:
                    class_name = _first_id_in_unit(units[1])
                if not class_name:
                    block_units = [c for c in block.children if c.type == "unit"]
                    if block_units:
                        class_name = _first_id_in_unit(block_units[0])

                if class_name:
                    qualified = f"{scope}.{class_name}" if scope else class_name
                    kind = "type" if first_kw in ("interface", "enum", "trait") else "class"
                    sym = Symbol(
                        id=make_symbol_id(filename, qualified, kind),
                        file=filename,
                        name=class_name,
                        qualified_name=qualified,
                        kind=kind,
                        language="groovy",
                        signature=f"{first_kw} {class_name}",
                        docstring="",
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        byte_offset=node.start_byte,
                        byte_length=node.end_byte - node.start_byte,
                        content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                    )
                    symbols.append(sym)
                    # Recurse into class body
                    _walk_commands(block.children, scope=qualified)
                continue

            # Method / function: has a unit containing a func node.
            # Two patterns:
            #   Interface/top-level: command > unit("type") + unit(func("name")) + ...
            #   Class method:        command > unit("type") + block(unit(func("name")) + {})
            # Check direct unit children first, then units inside the block.
            units_to_check = list(units)
            if block:
                units_to_check += [c for c in block.children if c.type == "unit"]
            for unit in units_to_check:
                method_name = _func_name_in_unit(unit)
                if method_name:
                    qualified = f"{scope}.{method_name}" if scope else method_name
                    kind = "method" if scope else "function"
                    # Build a readable signature from source
                    raw = source[node.start_byte:node.start_byte + min(120, node.end_byte - node.start_byte)]
                    sig = raw.split("{")[0].strip()
                    sym = Symbol(
                        id=make_symbol_id(filename, qualified, kind),
                        file=filename,
                        name=method_name,
                        qualified_name=qualified,
                        kind=kind,
                        language="groovy",
                        signature=sig,
                        docstring="",
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        byte_offset=node.start_byte,
                        byte_length=node.end_byte - node.start_byte,
                        content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                    )
                    symbols.append(sym)
                    break

    _walk_commands(tree.root_node.children)
    return symbols


def _parse_autohotkey_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from AutoHotkey v2 source files using regex line-scanning.

    AutoHotkey is not available in tree-sitter-language-pack, so this extractor
    uses regex patterns with brace-depth tracking to identify:

    - Top-level functions:  ``FuncName(params) {`` or ``FuncName(params) => expr``
    - Classes:              ``class ClassName [extends Base] {``
    - Methods:              indented ``[static] MethodName(params) {`` inside a class
    - Hotkeys:              ``F1::action``, ``#n::{ ... }``, ``^!Del::`` etc.
    - #HotIf directives:   ``#HotIf WinActive(...)`` / ``#HotIf`` (reset)

    Only declarations whose opening ``{`` (or fat-arrow ``=>``) appears on the
    same line are recognised; next-line-brace style is not supported for
    function/method detection (to avoid false positives on bare call sites).
    Class declarations whose ``{`` appears on the following line ARE handled
    correctly via speculative depth tracking.
    """
    import re

    source = source_bytes.decode("utf-8", errors="replace")
    lines = source.splitlines()
    symbols: list[Symbol] = []

    # class ClassName [extends Base] { optional comment
    CLASS_RE = re.compile(
        r'^\s*class\s+([A-Za-z_]\w*)(?:\s+extends\s+([A-Za-z_]\w*))?\s*(\{)?\s*(?:;.*)?$',
        re.IGNORECASE,
    )
    # [static] FuncName(params) { or => (declaration, not a bare call)
    FUNC_RE = re.compile(
        r'^(\s*)(static\s+)?([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:=>|\{)',
        re.IGNORECASE,
    )
    # Hotkey: Key:: or Key::Action (at top level, not indented inside a class)
    # Matches modifier combos like #n::, ^!Del::, F1::, ~RButton::RunScript()
    HOTKEY_RE = re.compile(
        r'^([~*$!^#+<>*&\w]+::(?:[^{;\s][^;]*?)?)\s*(?:;.*)?$',
    )
    # #HotIf [expression]
    HOTIF_RE = re.compile(
        r'^#HotIf(?:\s+(.+?))?\s*(?:;.*)?$',
        re.IGNORECASE,
    )
    _KEYWORDS = frozenset({
        "if", "while", "for", "loop", "catch", "switch", "try", "else",
        "class", "return", "throw", "until",
    })

    depth = 0
    # Stack of (class_name, min_depth_inside_class)
    class_stack: list[tuple[str, int]] = []

    def _current_class() -> "Optional[str]":
        return class_stack[-1][0] if class_stack else None

    for line_no, raw_line in enumerate(lines, start=1):
        # Strip inline ; comments for analysis (preserve original for nothing else)
        stripped = re.sub(r'\s*;[^\n]*$', '', raw_line).rstrip()
        if not stripped.strip():
            continue

        # ── Class declaration ─────────────────────────────────────────────
        cm = CLASS_RE.match(stripped)
        if cm:
            class_name = cm.group(1)
            extends = cm.group(2)
            has_brace = cm.group(3) is not None
            sig = f"class {class_name}"
            if extends:
                sig += f" extends {extends}"
            sym = Symbol(
                id=make_symbol_id(filename, class_name, "class"),
                file=filename,
                name=class_name,
                qualified_name=class_name,
                kind="class",
                language="autohotkey",
                signature=sig,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            if has_brace:
                depth += 1
                class_stack.append((class_name, depth))
            else:
                # Brace expected on next non-blank line; speculatively reserve depth+1
                class_stack.append((class_name, depth + 1))
            continue

        # ── Update brace depth for this line ──────────────────────────────
        opens = stripped.count("{")
        closes = stripped.count("}")
        depth += opens - closes
        # Pop classes whose body we have left
        while class_stack and depth < class_stack[-1][1]:
            class_stack.pop()

        # ── #HotIf directive ──────────────────────────────────────────────
        hif = HOTIF_RE.match(stripped)
        if hif:
            expr = (hif.group(1) or "").strip()
            # "#HotIf" alone resets the context; still worth indexing as a marker
            name = f"#HotIf {expr}" if expr else "#HotIf"
            sig = name
            sym = Symbol(
                id=make_symbol_id(filename, name, "constant"),
                file=filename,
                name=name,
                qualified_name=name,
                kind="constant",
                language="autohotkey",
                signature=sig,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        # ── Hotkey definition ─────────────────────────────────────────────
        # Only index at top level (depth == 0, no current class context)
        if not class_stack and depth == 0:
            hk = HOTKEY_RE.match(stripped)
            if hk:
                hotkey_def = hk.group(1)
                # Split into trigger and (optional) single-line action
                parts = hotkey_def.split("::", 1)
                trigger = parts[0]
                action = parts[1].strip() if len(parts) > 1 and parts[1].strip() else ""
                sig = f"{trigger}::{action}" if action else f"{trigger}::"
                sym = Symbol(
                    id=make_symbol_id(filename, sig, "constant"),
                    file=filename,
                    name=trigger,
                    qualified_name=sig,
                    kind="constant",
                    language="autohotkey",
                    signature=sig,
                    line=line_no,
                    end_line=line_no,
                )
                symbols.append(sym)
                continue

        # ── Function / method declaration ─────────────────────────────────
        fm = FUNC_RE.match(stripped)
        if not fm:
            continue
        indent = fm.group(1)
        is_static = bool(fm.group(2))
        func_name = fm.group(3)
        params = fm.group(4).strip()

        if func_name.lower() in _KEYWORDS:
            continue

        cls = _current_class()
        if cls and indent:
            qualified = f"{cls}.{func_name}"
            kind = "method"
            parent_id = make_symbol_id(filename, cls, "class")
        else:
            qualified = func_name
            kind = "function"
            parent_id = None

        prefix = "static " if is_static else ""
        sig = f"{prefix}{func_name}({params})"
        sym = Symbol(
            id=make_symbol_id(filename, qualified, kind),
            file=filename,
            name=func_name,
            qualified_name=qualified,
            kind=kind,
            language="autohotkey",
            signature=sig,
            parent=parent_id,
            line=line_no,
            end_line=line_no,
        )
        symbols.append(sym)

    return symbols


def _parse_xml_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse XML/XUL source and extract meaningful symbols.

    XML and XUL (Mozilla's XML User Interface Language) share the same
    tree-sitter-xml grammar.  Unlike code languages, XML has no functions
    or classes — the extractable symbols are:

      - Document root element (<window>, <page>, <root>) -> type symbol
      - Elements with id/name/key identity attributes -> constant symbols
        (id takes priority; name and key are checked as fallbacks)
        qualified_name encodes element type: tag::value (e.g. block::foundationConcrete)
      - <script src="..."> references -> function symbols

    Preceding <!-- ... --> comments are captured as docstrings.
    """
    try:
        parser = get_parser("xml")
    except Exception:
        return []

    tree = parser.parse(source_bytes)
    source = source_bytes.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []
    root_extracted = False

    def _tag_name(node) -> Optional[str]:
        """Extract the tag name from an element node.

        For element nodes, the tag name is the first Name child inside
        the STag or EmptyElemTag child.
        """
        for child in node.children:
            if child.type in ("STag", "EmptyElemTag"):
                for sub in child.children:
                    if sub.type == "Name":
                        return source[sub.start_byte:sub.end_byte]
                return None
        return None

    def _get_attr(node, attr_name: str) -> Optional[str]:
        """Get the value of a named attribute from an element node.

        Walks through the element's STag or EmptyElemTag to find
        Attribute children, then matches by Name and extracts AttValue.
        """
        for child in node.children:
            if child.type in ("STag", "EmptyElemTag"):
                for attr in child.children:
                    if attr.type == "Attribute":
                        a_name = None
                        a_value = None
                        for sub in attr.children:
                            if sub.type == "Name":
                                a_name = source[sub.start_byte:sub.end_byte]
                            elif sub.type == "AttValue":
                                # AttValue includes surrounding quotes
                                raw = source[sub.start_byte:sub.end_byte]
                                a_value = raw.strip('"').strip("'")
                        if a_name == attr_name and a_value is not None:
                            return a_value
        return None

    def _preceding_comment(node) -> str:
        """Collect preceding <!-- ... --> XML comment siblings as a docstring.

        In tree-sitter-xml, CharData whitespace nodes sit between Comment and
        element siblings, so we skip over them.  For root elements whose
        prev sibling is the prolog, we look for Comments inside the prolog.
        """
        lines: list[str] = []
        prev = node.prev_named_sibling

        # Skip CharData whitespace to find Comments
        while prev and prev.type == "CharData":
            prev = prev.prev_named_sibling

        # For root elements, comments may be inside the prolog
        if prev and prev.type == "prolog":
            # Walk prolog children in reverse to find trailing Comments
            for child in reversed(prev.children):
                if child.type == "Comment":
                    raw = source[child.start_byte:child.end_byte]
                    if raw.startswith("<!--"):
                        raw = raw[4:]
                    if raw.endswith("-->"):
                        raw = raw[:-3]
                    raw = raw.strip()
                    if raw:
                        lines.insert(0, raw)
                elif child.type != "CharData":
                    break  # Stop at non-comment, non-whitespace
            return "\n".join(lines) if lines else ""

        while prev and prev.type == "Comment":
            raw = source[prev.start_byte:prev.end_byte]
            # Strip <!-- and --> delimiters
            if raw.startswith("<!--"):
                raw = raw[4:]
            if raw.endswith("-->"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw:
                lines.insert(0, raw)
            prev = prev.prev_named_sibling
            # Skip CharData whitespace between consecutive comments
            while prev and prev.type == "CharData":
                prev = prev.prev_named_sibling
        return "\n".join(lines) if lines else ""

    def _walk(node) -> None:
        nonlocal root_extracted

        if node.type == "element":
            tag = _tag_name(node)
            if not tag:
                for child in node.children:
                    _walk(child)
                return

            # 1. Document root element -> type symbol
            if not root_extracted and node.parent and node.parent.type == "document":
                root_extracted = True
                # Build signature from tag + key attributes
                attrs = []
                elem_id = _get_attr(node, "id")
                title = _get_attr(node, "title")
                xmlns = _get_attr(node, "xmlns")
                if elem_id:
                    attrs.append(f'id="{elem_id}"')
                if title:
                    attrs.append(f'title="{title}"')
                if xmlns:
                    # Shorten long namespace URIs
                    short_ns = xmlns.rsplit("/", 1)[-1] if "/" in xmlns else xmlns
                    attrs.append(f'xmlns="...{short_ns}"')
                attr_str = " " + " ".join(attrs) if attrs else ""
                signature = f"<{tag}{attr_str}>"
                docstring = _preceding_comment(node)

                sym = Symbol(
                    id=make_symbol_id(filename, tag, "type"),
                    file=filename,
                    name=tag,
                    qualified_name=tag,
                    kind="type",
                    language="xml",
                    signature=signature,
                    docstring=docstring,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)

            # 2. <script src="..."> references -> function symbol
            if tag == "script":
                src = _get_attr(node, "src")
                if src:
                    name = src.rsplit("/", 1)[-1] if "/" in src else src
                    signature = f'<script src="{src}"/>'
                    docstring = _preceding_comment(node)

                    sym = Symbol(
                        id=make_symbol_id(filename, name, "function"),
                        file=filename,
                        name=name,
                        qualified_name=src,
                        kind="function",
                        language="xml",
                        signature=signature,
                        docstring=docstring,
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        byte_offset=node.start_byte,
                        byte_length=node.end_byte - node.start_byte,
                        content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                    )
                    symbols.append(sym)

            # 3. Elements with id/name/key attribute -> constant symbol
            # Priority: id > name > key (first match wins to avoid duplicates)
            elem_id = _get_attr(node, "id")
            elem_name = _get_attr(node, "name")
            elem_key = _get_attr(node, "key")
            ident_attr, ident_val = next(
                ((a, v) for a, v in (("id", elem_id), ("name", elem_name), ("key", elem_key)) if v),
                (None, None),
            )
            if ident_val:
                signature = f'<{tag} {ident_attr}="{ident_val}"/>'
                docstring = _preceding_comment(node)

                sym = Symbol(
                    id=make_symbol_id(filename, ident_val, "constant"),
                    file=filename,
                    name=ident_val,
                    qualified_name=f"{tag}::{ident_val}",
                    kind="constant",
                    language="xml",
                    signature=signature,
                    docstring=docstring,
                    line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    byte_offset=node.start_byte,
                    byte_length=node.end_byte - node.start_byte,
                    content_hash=compute_content_hash(source_bytes[node.start_byte:node.end_byte]),
                )
                symbols.append(sym)

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return symbols


def _parse_openapi_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Parse OpenAPI/Swagger spec and extract path operations and schemas as symbols.

    Extracts:
    - Path operations (GET /users, POST /users/{id}, ...) -> function symbols
    - Component schemas (v3) / definitions (v2)           -> type symbols

    Requires pyyaml for YAML files; JSON files use the stdlib json module.
    Returns [] gracefully if parsing fails or pyyaml is not installed.
    """
    source = source_bytes.decode("utf-8", errors="replace")
    is_json = filename.lower().endswith(".json")
    symbols: list[Symbol] = []

    # Parse structured data
    data: object = None
    if is_json:
        try:
            import json as _json
            data = _json.loads(source)
        except Exception:
            return symbols
    else:
        try:
            import yaml as _yaml  # optional dep; degrades gracefully
            data = _yaml.safe_load(source)
        except Exception:
            return symbols

    if not isinstance(data, dict):
        return symbols

    # Verify this is actually an OpenAPI/Swagger document
    if "openapi" not in data and "swagger" not in data and "paths" not in data:
        return symbols

    # Pre-compute per-line byte offsets for accurate line->byte mapping
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln.encode("utf-8")))

    def _find_line(text: str, after: int = 0) -> int:
        t = text.lower()
        for i in range(after, len(lines)):
            if t in lines[i].lower():
                return i + 1
        return max(after + 1, 1)

    def _byte_start(line_1based: int) -> int:
        idx = line_1based - 1
        return offsets[idx] if 0 <= idx < len(offsets) else 0

    HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head")

    # Path operations
    for path_str, path_item in (data.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_line = _find_line(str(path_str))
        for method in HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            symbol_name = f"{method.upper()} {path_str}"
            op_line = _find_line(method, path_line - 1)
            summary = (op.get("summary") or op.get("description") or "").strip()
            op_id = (op.get("operationId") or "").strip()
            signature = symbol_name
            if op_id:
                signature += f"  # {op_id}"
            elif summary:
                signature += f"  # {summary[:60]}"
            bs = _byte_start(op_line)
            sym = Symbol(
                id=make_symbol_id(filename, symbol_name, "function"),
                file=filename,
                name=symbol_name,
                qualified_name=symbol_name,
                kind="function",
                language="openapi",
                signature=signature,
                docstring=summary,
                line=op_line,
                end_line=op_line,
                byte_offset=bs,
                byte_length=len(signature.encode("utf-8")),
                content_hash=compute_content_hash(signature.encode("utf-8")),
            )
            symbols.append(sym)

    # Schemas: components/schemas (v3) or definitions (v2)
    schemas: dict = {}
    components = data.get("components") or {}
    if isinstance(components, dict):
        schemas = components.get("schemas") or {}
    if not schemas:
        schemas = data.get("definitions") or {}

    for schema_name, schema_def in (schemas or {}).items():
        if not isinstance(schema_def, dict):
            continue
        description = (schema_def.get("description") or "").strip()
        schema_type = schema_def.get("type", "object")
        signature = f"schema {schema_name}"
        if schema_type and schema_type != "object":
            signature += f": {schema_type}"
        schema_line = _find_line(str(schema_name))
        bs = _byte_start(schema_line)
        sym = Symbol(
            id=make_symbol_id(filename, schema_name, "type"),
            file=filename,
            name=schema_name,
            qualified_name=schema_name,
            kind="type",
            language="openapi",
            signature=signature,
            docstring=description,
            line=schema_line,
            end_line=schema_line,
            byte_offset=bs,
            byte_length=len(signature.encode("utf-8")),
            content_hash=compute_content_hash(signature.encode("utf-8")),
        )
        symbols.append(sym)

    return symbols


def _parse_asm_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract symbols from assembly source files using regex line-scanning.

    No tree-sitter grammar covers the breadth of assembler dialects used in
    retro and embedded development, so this extractor uses regex patterns to
    support multiple assembler syntaxes in a single pass:

    - **WLA-DX** (65816/Z80/6502/SPC700): ``.section``, ``.macro``/``.endm``,
      ``.define``/``.def``, ``.struct``/``.endst``, ``.enum``/``.ende``,
      ``.ramsection``, ``.proc``/``.endproc``
    - **NASM/YASM**: ``section``, ``%define``, ``%macro``/``%endmacro``,
      ``equ``, ``struc``/``endstruc``
    - **GAS (GNU Assembler)**: ``.text``/``.data``/``.bss``, ``.set``/``.equ``,
      ``.macro``/``.endm``, ``.type``
    - **CA65 (cc65)**: ``.segment``, ``.proc``/``.endproc``,
      ``.macro``/``.endmacro``, ``.define``

    Symbol mapping:
      - Labels (``name:``) -> **function** (local ``_``-prefixed labels excluded)
      - Sections (``.section``, ``section``, ``.segment``) -> **class**
      - Macros (``.macro``, ``%macro``) -> **function**
      - Constants (``.define``, ``.def``, ``.set``, ``.equ``, ``%define``, ``equ``) -> **constant**
      - Structs (``.struct``, ``struc``) -> **type**
      - Procedures (``.proc``) -> **function**
      - Named enum members inside ``.enum``/``.ende`` -> **constant**

    Preceding ``;``-style comments are captured as docstrings.
    """
    import re

    source = source_bytes.decode("utf-8", errors="replace")
    lines = source.splitlines()
    symbols: list[Symbol] = []

    # --- Regex patterns ---

    # Labels: "name:" at column 0 (no leading whitespace = global label)
    # Excludes anonymous labels (+, -, ++, etc.) and _prefixed local labels
    LABEL_RE = re.compile(
        r'^([A-Za-z][A-Za-z0-9_.]*)\s*:',
    )

    # Sections: .section "name" [type], .ramsection "name" [...]
    SECTION_RE = re.compile(
        r'^\s*\.(?:section|ramsection)\s+"([^"]+)"',
        re.IGNORECASE,
    )
    # NASM-style: section .text / section .data / section .bss
    NASM_SECTION_RE = re.compile(
        r'^\s*section\s+(\.\w+)',
        re.IGNORECASE,
    )
    # CA65-style: .segment "CODE"
    CA65_SEGMENT_RE = re.compile(
        r'^\s*\.segment\s+"([^"]+)"',
        re.IGNORECASE,
    )

    # Macros: .macro NAME, %macro NAME [nargs]
    MACRO_START_RE = re.compile(
        r'^\s*[.%](?:macro|macrocall)\s+([A-Za-z_]\w*)',
        re.IGNORECASE,
    )
    MACRO_END_RE = re.compile(
        r'^\s*[.%](?:endm|endmacro)\b',
        re.IGNORECASE,
    )

    # Constants: .define NAME value, .def NAME value
    WLADX_DEFINE_RE = re.compile(
        r'^\s*\.(?:define|def)\s+([A-Za-z_][A-Za-z0-9_.]*)\s+(.*)',
        re.IGNORECASE,
    )
    # GAS style: .set NAME, value / .equ NAME, value
    GAS_CONST_RE = re.compile(
        r'^\s*\.(?:set|equ)\s+([A-Za-z_]\w*)\s*,\s*(.*)',
        re.IGNORECASE,
    )
    # NASM style: %define NAME value
    NASM_DEFINE_RE = re.compile(
        r'^\s*%define\s+([A-Za-z_]\w*)\s*(.*)',
        re.IGNORECASE,
    )
    # EQU constant: NAME equ VALUE or NAME = VALUE (may be indented)
    EQU_RE = re.compile(
        r'^\s*([A-Za-z_][A-Za-z0-9_.]*)\s+(?:equ|EQU|=)\s+(.*)',
    )

    # Structs: .struct NAME, .STRUCT NAME, struc NAME (NASM)
    STRUCT_START_RE = re.compile(
        r'^\s*\.?(?:struct|struc)\s+([A-Za-z_]\w*)',
        re.IGNORECASE,
    )
    STRUCT_END_RE = re.compile(
        r'^\s*\.?(?:endst|endstruc|ends)\b',
        re.IGNORECASE,
    )

    # Enums: .enum [value] [export] (WLA-DX)
    ENUM_START_RE = re.compile(
        r'^\s*\.enum\b',
        re.IGNORECASE,
    )
    ENUM_END_RE = re.compile(
        r'^\s*\.ende\b',
        re.IGNORECASE,
    )
    # Enum member: NAME db/dw/ds/dsb/dsw (WLA-DX enum body syntax)
    ENUM_MEMBER_RE = re.compile(
        r'^([A-Za-z_][A-Za-z0-9_.]*)\s+(?:db|dw|dl|ds|dsb|dsw)\b',
    )

    # Procedures: .proc NAME (CA65 / WLA-DX)
    PROC_RE = re.compile(
        r'^\s*\.proc\s+([A-Za-z_]\w*)',
        re.IGNORECASE,
    )

    # Comment line (for docstring extraction): ; or @ prefixed
    COMMENT_RE = re.compile(r'^\s*[;@]\s?(.*)')

    # --- State tracking ---
    current_section: Optional[str] = None
    current_section_id: Optional[str] = None
    in_struct = False
    in_enum = False
    in_macro = False
    in_block_comment = False
    pending_comments: list[str] = []

    def _flush_docstring() -> str:
        """Collect pending comment lines into a docstring and clear."""
        if not pending_comments:
            return ""
        doc = "\n".join(pending_comments)
        pending_comments.clear()
        return doc

    def _make_qualified(name: str) -> str:
        """Qualify a symbol name with the current section."""
        if current_section:
            return f"{current_section}::{name}"
        return name

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        stripped = line.strip()

        # --- C-style block comment tracking (/* ... */) ---
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped[2:]:
                in_block_comment = True
            continue

        # Blank lines reset pending comment accumulation
        if not stripped:
            pending_comments.clear()
            continue

        # --- Collect comments for docstrings ---
        cm = COMMENT_RE.match(line)
        if cm and not in_struct and not in_enum:
            pending_comments.append(cm.group(1).rstrip())
            continue

        # --- Struct end ---
        if in_struct and STRUCT_END_RE.match(line):
            in_struct = False
            pending_comments.clear()
            continue

        # --- Struct start ---
        sm = STRUCT_START_RE.match(line)
        if sm and not in_struct and not in_macro:
            struct_name = sm.group(1)
            docstring = _flush_docstring()
            sym = Symbol(
                id=make_symbol_id(filename, struct_name, "type"),
                file=filename,
                name=struct_name,
                qualified_name=struct_name,
                kind="type",
                language="asm",
                signature=f".struct {struct_name}",
                docstring=docstring,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            in_struct = True
            continue

        # Inside a struct body — skip field definitions
        if in_struct:
            pending_comments.clear()
            continue

        # --- Enum end ---
        if in_enum and ENUM_END_RE.match(line):
            in_enum = False
            pending_comments.clear()
            continue

        # --- Enum start ---
        if ENUM_START_RE.match(line) and not in_enum and not in_macro:
            in_enum = True
            pending_comments.clear()
            continue

        # --- Enum members ---
        if in_enum:
            em_match = ENUM_MEMBER_RE.match(line.strip())
            if em_match:
                member_name = em_match.group(1)
                docstring = _flush_docstring()
                sym = Symbol(
                    id=make_symbol_id(filename, member_name, "constant"),
                    file=filename,
                    name=member_name,
                    qualified_name=member_name,
                    kind="constant",
                    language="asm",
                    signature=member_name,
                    docstring=docstring,
                    line=line_no,
                    end_line=line_no,
                )
                symbols.append(sym)
            pending_comments.clear()
            continue

        # --- Macro end ---
        if in_macro and MACRO_END_RE.match(line):
            in_macro = False
            pending_comments.clear()
            continue

        # Inside a macro body — skip template content
        if in_macro:
            pending_comments.clear()
            continue

        # --- Macro start ---
        mm = MACRO_START_RE.match(line)
        if mm:
            macro_name = mm.group(1)
            docstring = _flush_docstring()
            sym = Symbol(
                id=make_symbol_id(filename, macro_name, "function"),
                file=filename,
                name=macro_name,
                qualified_name=macro_name,
                kind="function",
                language="asm",
                signature=f".macro {macro_name}",
                docstring=docstring,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            in_macro = True
            continue

        # --- Section / segment ---
        sec = SECTION_RE.match(line)
        if not sec:
            sec = NASM_SECTION_RE.match(line)
        if not sec:
            sec = CA65_SEGMENT_RE.match(line)
        if sec:
            section_name = sec.group(1)
            docstring = _flush_docstring()
            current_section = section_name
            current_section_id = make_symbol_id(filename, section_name, "class")
            sym = Symbol(
                id=current_section_id,
                file=filename,
                name=section_name,
                qualified_name=section_name,
                kind="class",
                language="asm",
                signature=line.strip(),
                docstring=docstring,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        # --- Section end (.ends) resets section context ---
        if re.match(r'^\s*\.ends\b', line, re.IGNORECASE):
            current_section = None
            current_section_id = None
            pending_comments.clear()
            continue

        # --- Procedure (.proc NAME) ---
        pm = PROC_RE.match(line)
        if pm:
            proc_name = pm.group(1)
            docstring = _flush_docstring()
            qualified = _make_qualified(proc_name)
            sym = Symbol(
                id=make_symbol_id(filename, qualified, "function"),
                file=filename,
                name=proc_name,
                qualified_name=qualified,
                kind="function",
                language="asm",
                signature=f".proc {proc_name}",
                docstring=docstring,
                parent=current_section_id,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        # --- Constants ---
        const_match = WLADX_DEFINE_RE.match(line)
        if not const_match:
            const_match = GAS_CONST_RE.match(line)
        if not const_match:
            const_match = NASM_DEFINE_RE.match(line)
        if const_match:
            const_name = const_match.group(1)
            const_value = const_match.group(2).split(";")[0].strip()
            docstring = _flush_docstring()
            sym = Symbol(
                id=make_symbol_id(filename, const_name, "constant"),
                file=filename,
                name=const_name,
                qualified_name=const_name,
                kind="constant",
                language="asm",
                signature=f"{const_name} = {const_value}" if const_value else const_name,
                docstring=docstring,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        equ_match = EQU_RE.match(line)
        if equ_match:
            const_name = equ_match.group(1)
            const_value = equ_match.group(2).split(";")[0].strip()
            docstring = _flush_docstring()
            sym = Symbol(
                id=make_symbol_id(filename, const_name, "constant"),
                file=filename,
                name=const_name,
                qualified_name=const_name,
                kind="constant",
                language="asm",
                signature=f"{const_name} = {const_value}" if const_value else const_name,
                docstring=docstring,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        # --- Labels (name:) ---
        lm = LABEL_RE.match(line)
        if lm:
            label_name = lm.group(1)
            # Skip local labels (_prefixed in WLA-DX — scoped to section)
            if label_name.startswith("_"):
                pending_comments.clear()
                continue
            docstring = _flush_docstring()
            qualified = _make_qualified(label_name)
            sym = Symbol(
                id=make_symbol_id(filename, qualified, "function"),
                file=filename,
                name=label_name,
                qualified_name=qualified,
                kind="function",
                language="asm",
                signature=f"{label_name}:",
                docstring=docstring,
                parent=current_section_id,
                line=line_no,
                end_line=line_no,
            )
            symbols.append(sym)
            continue

        # Non-matching line — clear pending comments
        pending_comments.clear()

    return symbols
