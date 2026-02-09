"""Generic AST symbol extractor using tree-sitter."""

from typing import Optional
from tree_sitter_language_pack import get_language, get_parser

from .symbols import Symbol, make_symbol_id
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
    
    spec = LANGUAGE_REGISTRY[language]
    source_bytes = content.encode("utf-8")
    
    # Get parser for this language
    parser = get_parser(spec.ts_language)
    tree = parser.parse(source_bytes)
    
    symbols = []
    _walk_tree(tree.root_node, spec, source_bytes, filename, language, symbols, None)
    
    return symbols


def _walk_tree(
    node,
    spec: LanguageSpec,
    source_bytes: bytes,
    filename: str,
    language: str,
    symbols: list,
    parent_symbol: Optional[Symbol] = None
):
    """Recursively walk the AST and extract symbols."""
    # Check if this node is a symbol
    if node.type in spec.symbol_node_types:
        symbol = _extract_symbol(
            node, spec, source_bytes, filename, language, parent_symbol
        )
        if symbol:
            symbols.append(symbol)
            parent_symbol = symbol
    
    # Check for constant patterns (top-level assignments with UPPER_CASE names)
    if node.type in spec.constant_patterns and parent_symbol is None:
        const_symbol = _extract_constant(node, spec, source_bytes, filename, language)
        if const_symbol:
            symbols.append(const_symbol)
    
    # Recurse into children
    for child in node.children:
        _walk_tree(child, spec, source_bytes, filename, language, symbols, parent_symbol)


def _extract_symbol(
    node,
    spec: LanguageSpec,
    source_bytes: bytes,
    filename: str,
    language: str,
    parent_symbol: Optional[Symbol] = None
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
    if parent_symbol:
        qualified_name = f"{parent_symbol.name}.{name}"
        kind = "method" if kind == "function" else kind
    else:
        qualified_name = name
    
    # Build signature
    signature = _build_signature(node, spec, source_bytes)
    
    # Extract docstring
    docstring = _extract_docstring(node, spec, source_bytes)
    
    # Extract decorators
    decorators = _extract_decorators(node, spec, source_bytes)
    
    # Create symbol
    symbol = Symbol(
        id=make_symbol_id(filename, qualified_name),
        file=filename,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        language=language,
        signature=signature,
        docstring=docstring,
        decorators=decorators,
        parent=parent_symbol.id if parent_symbol else None,
        line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        byte_offset=node.start_byte,
        byte_length=node.end_byte - node.start_byte,
    )
    
    return symbol


def _extract_name(node, spec: LanguageSpec, source_bytes: bytes) -> Optional[str]:
    """Extract the name from an AST node."""
    # Handle special cases first
    if node.type == "arrow_function":
        # Arrow functions get name from parent variable_declarator
        return None
    
    # Handle type_declaration in Go - name is in type_spec child
    if node.type == "type_declaration":
        for child in node.children:
            if child.type == "type_spec":
                name_node = child.child_by_field_name("name")
                if name_node:
                    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
        return None
    
    if node.type not in spec.name_fields:
        return None
    
    field_name = spec.name_fields[node.type]
    name_node = node.child_by_field_name(field_name)
    
    if name_node:
        return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
    
    return None


def _build_signature(node, spec: LanguageSpec, source_bytes: bytes) -> str:
    """Build a clean signature from AST node."""
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
    
    # Walk backwards through siblings
    prev = node.prev_named_sibling
    while prev and prev.type in ("comment", "line_comment", "block_comment"):
        comment_text = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8")
        comments.insert(0, comment_text)
        prev = prev.prev_named_sibling
    
    if not comments:
        return ""
    
    docstring = "\n".join(comments)
    return _clean_comment_markers(docstring)


def _clean_comment_markers(text: str) -> str:
    """Clean comment markers from docstring."""
    lines = text.split("\n")
    cleaned = []
    
    for line in lines:
        line = line.strip()
        # Remove leading comment markers
        if line.startswith("/**"):
            line = line[3:]
        elif line.startswith("/*"):
            line = line[2:]
        elif line.startswith("///"):
            line = line[3:]
        elif line.startswith("//"):
            line = line[2:]
        elif line.startswith("//!"):
            line = line[3:]
        elif line.startswith("*"):
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
    
    # Walk backwards through siblings to find decorators
    prev = node.prev_named_sibling
    while prev and prev.type == spec.decorator_node_type:
        decorator_text = source_bytes[prev.start_byte:prev.end_byte].decode("utf-8")
        decorators.insert(0, decorator_text.strip())
        prev = prev.prev_named_sibling
    
    return decorators


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
                
                return Symbol(
                    id=make_symbol_id(filename, name),
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
                )
    
    return None
