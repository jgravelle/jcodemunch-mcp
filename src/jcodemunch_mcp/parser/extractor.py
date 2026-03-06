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
    if node.type == "variable_declarator" and language in ("javascript", "typescript"):
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
    if node.type == "type_alias":
        for child in node.children:
            if child.type == "type_identifier":
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8")
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

# Directives that define named "symbols" in a Blade template
_BLADE_SYMBOL_PATTERNS: list[tuple[str, str, str]] = [
    # (kind, directive_regex, capture_group_name)
    # @extends('layouts.app')  → type  "layouts.app"
    ("type",     r"@extends\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @section('content')      → method "content"
    ("method",   r"@section\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @component('alert')      → class  "alert"
    ("class",    r"@component\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @include('partials.nav') → function "partials.nav"
    ("function", r"@include(?:If|When|Unless|First)?\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @push('scripts')         → constant "scripts"
    ("constant", r"@push\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @stack('scripts')        → constant "scripts" (declaration side)
    ("constant", r"@stack\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @slot('title')           → method "title"
    ("method",   r"@slot\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @yield('content')        → method "content"
    ("method",   r"@yield\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
    # @livewire('counter')     → class "counter"
    ("class",    r"@livewire\s*\(\s*['\"](?P<name>[^'\"]+)['\"]", "name"),
]

# Compiled once at module load
_BLADE_COMPILED: list[tuple[str, re.Pattern, str]] = [
    (kind, re.compile(pattern, re.IGNORECASE), group)
    for kind, pattern, group in _BLADE_SYMBOL_PATTERNS
]


def _parse_blade_symbols(source_bytes: bytes, filename: str) -> list[Symbol]:
    """Extract Blade template symbols using regex.

    Since tree-sitter has no Blade grammar, we scan for directives that
    define meaningful structural elements: @extends, @section, @component,
    @include*, @push, @stack, @slot, @yield, @livewire.
    """
    content = source_bytes.decode("utf-8", errors="replace")
    lines = content.splitlines()

    # Build a byte-offset → line-number lookup (1-indexed)
    line_start_offsets: list[int] = []
    offset = 0
    for line in lines:
        line_start_offsets.append(offset)
        offset += len(line.encode("utf-8")) + 1  # +1 for \n

    def byte_to_line(byte_pos: int) -> int:
        lo, hi = 0, len(line_start_offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_start_offsets[mid] <= byte_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1  # 1-indexed

    symbols: list[Symbol] = []
    seen: set[tuple[str, str]] = set()  # (kind, name) dedup per file

    for kind, pattern, group in _BLADE_COMPILED:
        for m in pattern.finditer(content):
            name = m.group(group)
            key = (kind, name)
            if key in seen:
                continue
            seen.add(key)

            line_no = byte_to_line(m.start())
            directive_text = m.group(0)
            qualified_name = name

            sym_bytes = directive_text.encode("utf-8")
            symbols.append(Symbol(
                id=make_symbol_id(filename, qualified_name, kind),
                file=filename,
                name=name,
                qualified_name=qualified_name,
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

    # Sort by line number for stable output
    symbols.sort(key=lambda s: s.line)
    return symbols
