namespace JCodeMunch.Mcp.Parser;

/// <summary>
/// Specification for extracting symbols from a language's AST.
/// Port of Python parser/languages.py LanguageSpec.
/// </summary>
public sealed record LanguageSpec
{
    /// <summary>tree-sitter language name</summary>
    public required string TsLanguage { get; init; }

    /// <summary>Node types that represent extractable symbols. Maps node_type -> symbol kind.</summary>
    public required Dictionary<string, string> SymbolNodeTypes { get; init; }

    /// <summary>Maps node_type -> child field name containing the name.</summary>
    public required Dictionary<string, string> NameFields { get; init; }

    /// <summary>Maps node_type -> child field name for parameters.</summary>
    public required Dictionary<string, string> ParamFields { get; init; }

    /// <summary>Maps node_type -> child field name for return type.</summary>
    public required Dictionary<string, string> ReturnTypeFields { get; init; }

    /// <summary>Docstring extraction strategy.</summary>
    public required string DocstringStrategy { get; init; }

    /// <summary>Decorator/attribute node type (if any).</summary>
    public string? DecoratorNodeType { get; init; }

    /// <summary>Node types that indicate nesting (methods inside classes).</summary>
    public required List<string> ContainerNodeTypes { get; init; }

    /// <summary>Node types for constants.</summary>
    public required List<string> ConstantPatterns { get; init; }

    /// <summary>Node types for type definitions.</summary>
    public required List<string> TypePatterns { get; init; }

    /// <summary>
    /// If True, decorators are direct children of the declaration node (e.g. C#).
    /// If False (default), decorators are preceding siblings.
    /// </summary>
    public bool DecoratorFromChildren { get; init; }
}

/// <summary>
/// Language registry with all supported languages and extension mappings.
/// Port of Python parser/languages.py.
/// </summary>
public static class LanguageRegistry
{
    /// <summary>File extension to language mapping.</summary>
    public static readonly Dictionary<string, string> LanguageExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        [".py"] = "python",
        [".js"] = "javascript",
        [".jsx"] = "javascript",
        [".ts"] = "typescript",
        [".tsx"] = "typescript",
        [".go"] = "go",
        [".rs"] = "rust",
        [".java"] = "java",
        [".php"] = "php",
        [".dart"] = "dart",
        [".cs"] = "csharp",
        [".c"] = "c",
        [".h"] = "cpp",
        [".cpp"] = "cpp",
        [".cc"] = "cpp",
        [".cxx"] = "cpp",
        [".hpp"] = "cpp",
        [".hh"] = "cpp",
        [".hxx"] = "cpp",
        [".swift"] = "swift",
        [".ex"] = "elixir",
        [".exs"] = "elixir",
        [".rb"] = "ruby",
        [".rake"] = "ruby",
        [".pl"] = "perl",
        [".pm"] = "perl",
        [".t"] = "perl",
    };

    // --- Language Specifications ---

    public static readonly LanguageSpec Python = new()
    {
        TsLanguage = "python",
        SymbolNodeTypes = new() { ["function_definition"] = "function", ["class_definition"] = "class" },
        NameFields = new() { ["function_definition"] = "name", ["class_definition"] = "name" },
        ParamFields = new() { ["function_definition"] = "parameters" },
        ReturnTypeFields = new() { ["function_definition"] = "return_type" },
        DocstringStrategy = "next_sibling_string",
        DecoratorNodeType = "decorator",
        ContainerNodeTypes = ["class_definition"],
        ConstantPatterns = ["assignment"],
        TypePatterns = ["type_alias_statement"],
    };

    public static readonly LanguageSpec JavaScript = new()
    {
        TsLanguage = "javascript",
        SymbolNodeTypes = new()
        {
            ["function_declaration"] = "function",
            ["class_declaration"] = "class",
            ["method_definition"] = "method",
            ["generator_function_declaration"] = "function",
        },
        NameFields = new()
        {
            ["function_declaration"] = "name",
            ["class_declaration"] = "name",
            ["method_definition"] = "name",
        },
        ParamFields = new()
        {
            ["function_declaration"] = "parameters",
            ["method_definition"] = "parameters",
            ["arrow_function"] = "parameters",
        },
        ReturnTypeFields = new(),
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = ["class_declaration", "class"],
        ConstantPatterns = ["lexical_declaration"],
        TypePatterns = [],
    };

    public static readonly LanguageSpec TypeScript = new()
    {
        TsLanguage = "typescript",
        SymbolNodeTypes = new()
        {
            ["function_declaration"] = "function",
            ["class_declaration"] = "class",
            ["method_definition"] = "method",
            ["interface_declaration"] = "type",
            ["type_alias_declaration"] = "type",
            ["enum_declaration"] = "type",
        },
        NameFields = new()
        {
            ["function_declaration"] = "name",
            ["class_declaration"] = "name",
            ["method_definition"] = "name",
            ["interface_declaration"] = "name",
            ["type_alias_declaration"] = "name",
            ["enum_declaration"] = "name",
        },
        ParamFields = new()
        {
            ["function_declaration"] = "parameters",
            ["method_definition"] = "parameters",
            ["arrow_function"] = "parameters",
        },
        ReturnTypeFields = new()
        {
            ["function_declaration"] = "return_type",
            ["method_definition"] = "return_type",
            ["arrow_function"] = "return_type",
        },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "decorator",
        ContainerNodeTypes = ["class_declaration", "class"],
        ConstantPatterns = ["lexical_declaration"],
        TypePatterns = ["interface_declaration", "type_alias_declaration", "enum_declaration"],
    };

    public static readonly LanguageSpec Go = new()
    {
        TsLanguage = "go",
        SymbolNodeTypes = new()
        {
            ["function_declaration"] = "function",
            ["method_declaration"] = "method",
            ["type_declaration"] = "type",
        },
        NameFields = new()
        {
            ["function_declaration"] = "name",
            ["method_declaration"] = "name",
            ["type_declaration"] = "name",
        },
        ParamFields = new()
        {
            ["function_declaration"] = "parameters",
            ["method_declaration"] = "parameters",
        },
        ReturnTypeFields = new()
        {
            ["function_declaration"] = "result",
            ["method_declaration"] = "result",
        },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = [],
        ConstantPatterns = ["const_declaration"],
        TypePatterns = ["type_declaration"],
    };

    public static readonly LanguageSpec Rust = new()
    {
        TsLanguage = "rust",
        SymbolNodeTypes = new()
        {
            ["function_item"] = "function",
            ["struct_item"] = "type",
            ["enum_item"] = "type",
            ["trait_item"] = "type",
            ["impl_item"] = "class",
            ["type_item"] = "type",
        },
        NameFields = new()
        {
            ["function_item"] = "name",
            ["struct_item"] = "name",
            ["enum_item"] = "name",
            ["trait_item"] = "name",
            ["type_item"] = "name",
        },
        ParamFields = new() { ["function_item"] = "parameters" },
        ReturnTypeFields = new() { ["function_item"] = "return_type" },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "attribute_item",
        ContainerNodeTypes = ["impl_item", "trait_item"],
        ConstantPatterns = ["const_item", "static_item"],
        TypePatterns = ["struct_item", "enum_item", "trait_item", "type_item"],
    };

    public static readonly LanguageSpec Java = new()
    {
        TsLanguage = "java",
        SymbolNodeTypes = new()
        {
            ["method_declaration"] = "method",
            ["constructor_declaration"] = "method",
            ["class_declaration"] = "class",
            ["interface_declaration"] = "type",
            ["enum_declaration"] = "type",
        },
        NameFields = new()
        {
            ["method_declaration"] = "name",
            ["constructor_declaration"] = "name",
            ["class_declaration"] = "name",
            ["interface_declaration"] = "name",
            ["enum_declaration"] = "name",
        },
        ParamFields = new()
        {
            ["method_declaration"] = "parameters",
            ["constructor_declaration"] = "parameters",
        },
        ReturnTypeFields = new() { ["method_declaration"] = "type" },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "marker_annotation",
        ContainerNodeTypes = ["class_declaration", "interface_declaration", "enum_declaration"],
        ConstantPatterns = ["field_declaration"],
        TypePatterns = ["interface_declaration", "enum_declaration"],
    };

    public static readonly LanguageSpec Php = new()
    {
        TsLanguage = "php",
        SymbolNodeTypes = new()
        {
            ["function_definition"] = "function",
            ["class_declaration"] = "class",
            ["method_declaration"] = "method",
            ["interface_declaration"] = "type",
            ["trait_declaration"] = "type",
            ["enum_declaration"] = "type",
        },
        NameFields = new()
        {
            ["function_definition"] = "name",
            ["class_declaration"] = "name",
            ["method_declaration"] = "name",
            ["interface_declaration"] = "name",
            ["trait_declaration"] = "name",
            ["enum_declaration"] = "name",
        },
        ParamFields = new()
        {
            ["function_definition"] = "parameters",
            ["method_declaration"] = "parameters",
        },
        ReturnTypeFields = new()
        {
            ["function_definition"] = "return_type",
            ["method_declaration"] = "return_type",
        },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "attribute",
        ContainerNodeTypes = ["class_declaration", "trait_declaration", "interface_declaration"],
        ConstantPatterns = ["const_declaration"],
        TypePatterns = ["interface_declaration", "trait_declaration", "enum_declaration"],
    };

    public static readonly LanguageSpec Dart = new()
    {
        TsLanguage = "dart",
        SymbolNodeTypes = new()
        {
            ["function_signature"] = "function",
            ["class_definition"] = "class",
            ["mixin_declaration"] = "class",
            ["enum_declaration"] = "type",
            ["extension_declaration"] = "class",
            ["method_signature"] = "method",
            ["type_alias"] = "type",
        },
        NameFields = new()
        {
            ["function_signature"] = "name",
            ["class_definition"] = "name",
            ["enum_declaration"] = "name",
            ["extension_declaration"] = "name",
        },
        ParamFields = new() { ["function_signature"] = "parameters" },
        ReturnTypeFields = new(),
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "annotation",
        ContainerNodeTypes = ["class_definition", "mixin_declaration", "extension_declaration"],
        ConstantPatterns = [],
        TypePatterns = ["type_alias", "enum_declaration"],
    };

    public static readonly LanguageSpec CSharp = new()
    {
        TsLanguage = "csharp",
        SymbolNodeTypes = new()
        {
            ["class_declaration"] = "class",
            ["record_declaration"] = "class",
            ["interface_declaration"] = "type",
            ["enum_declaration"] = "type",
            ["struct_declaration"] = "type",
            ["delegate_declaration"] = "type",
            ["method_declaration"] = "method",
            ["constructor_declaration"] = "method",
        },
        NameFields = new()
        {
            ["class_declaration"] = "name",
            ["record_declaration"] = "name",
            ["interface_declaration"] = "name",
            ["enum_declaration"] = "name",
            ["struct_declaration"] = "name",
            ["delegate_declaration"] = "name",
            ["method_declaration"] = "name",
            ["constructor_declaration"] = "name",
        },
        ParamFields = new()
        {
            ["method_declaration"] = "parameters",
            ["constructor_declaration"] = "parameters",
            ["delegate_declaration"] = "parameters",
        },
        ReturnTypeFields = new() { ["method_declaration"] = "returns" },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = "attribute_list",
        DecoratorFromChildren = true,
        ContainerNodeTypes = ["class_declaration", "struct_declaration", "record_declaration", "interface_declaration"],
        ConstantPatterns = [],
        TypePatterns = ["interface_declaration", "enum_declaration", "struct_declaration", "delegate_declaration", "record_declaration"],
    };

    public static readonly LanguageSpec C = new()
    {
        TsLanguage = "c",
        SymbolNodeTypes = new()
        {
            ["function_definition"] = "function",
            ["struct_specifier"] = "type",
            ["enum_specifier"] = "type",
            ["union_specifier"] = "type",
            ["type_definition"] = "type",
        },
        NameFields = new()
        {
            ["function_definition"] = "declarator",
            ["struct_specifier"] = "name",
            ["enum_specifier"] = "name",
            ["union_specifier"] = "name",
            ["type_definition"] = "declarator",
        },
        ParamFields = new() { ["function_definition"] = "declarator" },
        ReturnTypeFields = new() { ["function_definition"] = "type" },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = [],
        ConstantPatterns = ["preproc_def"],
        TypePatterns = ["type_definition", "enum_specifier", "struct_specifier", "union_specifier"],
    };

    public static readonly LanguageSpec Cpp = new()
    {
        TsLanguage = "cpp",
        SymbolNodeTypes = new()
        {
            ["class_specifier"] = "class",
            ["struct_specifier"] = "type",
            ["union_specifier"] = "type",
            ["enum_specifier"] = "type",
            ["type_definition"] = "type",
            ["alias_declaration"] = "type",
            ["function_definition"] = "function",
            ["declaration"] = "function",
            ["field_declaration"] = "function",
        },
        NameFields = new()
        {
            ["class_specifier"] = "name",
            ["struct_specifier"] = "name",
            ["union_specifier"] = "name",
            ["enum_specifier"] = "name",
            ["type_definition"] = "declarator",
            ["alias_declaration"] = "name",
            ["function_definition"] = "declarator",
            ["declaration"] = "declarator",
            ["field_declaration"] = "declarator",
        },
        ParamFields = new()
        {
            ["function_definition"] = "declarator",
            ["declaration"] = "declarator",
            ["field_declaration"] = "declarator",
        },
        ReturnTypeFields = new()
        {
            ["function_definition"] = "type",
            ["declaration"] = "type",
            ["field_declaration"] = "type",
        },
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = ["class_specifier", "struct_specifier", "union_specifier"],
        ConstantPatterns = ["preproc_def"],
        TypePatterns = ["class_specifier", "struct_specifier", "union_specifier", "enum_specifier", "type_definition", "alias_declaration"],
    };

    public static readonly LanguageSpec Swift = new()
    {
        TsLanguage = "swift",
        SymbolNodeTypes = new()
        {
            ["function_declaration"] = "function",
            ["class_declaration"] = "class",
            ["protocol_declaration"] = "type",
            ["init_declaration"] = "method",
        },
        NameFields = new()
        {
            ["function_declaration"] = "name",
            ["class_declaration"] = "name",
            ["protocol_declaration"] = "name",
            ["init_declaration"] = "name",
        },
        ParamFields = new(),
        ReturnTypeFields = new(),
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = ["class_declaration", "protocol_declaration"],
        ConstantPatterns = ["property_declaration"],
        TypePatterns = ["protocol_declaration"],
    };

    public static readonly LanguageSpec Elixir = new()
    {
        TsLanguage = "elixir",
        SymbolNodeTypes = new(),
        NameFields = new(),
        ParamFields = new(),
        ReturnTypeFields = new(),
        DocstringStrategy = "elixir",
        DecoratorNodeType = null,
        ContainerNodeTypes = [],
        ConstantPatterns = [],
        TypePatterns = [],
    };

    public static readonly LanguageSpec Ruby = new()
    {
        TsLanguage = "ruby",
        SymbolNodeTypes = new()
        {
            ["method"] = "function",
            ["singleton_method"] = "function",
            ["class"] = "class",
            ["module"] = "type",
        },
        NameFields = new()
        {
            ["method"] = "name",
            ["singleton_method"] = "name",
            ["class"] = "name",
            ["module"] = "name",
        },
        ParamFields = new()
        {
            ["method"] = "parameters",
            ["singleton_method"] = "parameters",
        },
        ReturnTypeFields = new(),
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = ["class", "module"],
        ConstantPatterns = [],
        TypePatterns = ["module"],
    };

    public static readonly LanguageSpec Perl = new()
    {
        TsLanguage = "perl",
        SymbolNodeTypes = new()
        {
            ["subroutine_declaration_statement"] = "function",
            ["package_statement"] = "class",
        },
        NameFields = new()
        {
            ["subroutine_declaration_statement"] = "name",
            ["package_statement"] = "name",
        },
        ParamFields = new(),
        ReturnTypeFields = new(),
        DocstringStrategy = "preceding_comment",
        DecoratorNodeType = null,
        ContainerNodeTypes = [],
        ConstantPatterns = ["use_statement"],
        TypePatterns = [],
    };

    /// <summary>All registered language specs keyed by language name.</summary>
    public static readonly Dictionary<string, LanguageSpec> Registry = new(StringComparer.OrdinalIgnoreCase)
    {
        ["python"] = Python,
        ["javascript"] = JavaScript,
        ["typescript"] = TypeScript,
        ["go"] = Go,
        ["rust"] = Rust,
        ["java"] = Java,
        ["php"] = Php,
        ["dart"] = Dart,
        ["csharp"] = CSharp,
        ["c"] = C,
        ["swift"] = Swift,
        ["cpp"] = Cpp,
        ["elixir"] = Elixir,
        ["ruby"] = Ruby,
        ["perl"] = Perl,
    };

    /// <summary>Get language name for a file based on extension, or null if unsupported.</summary>
    public static string? GetLanguageForFile(string filePath)
    {
        var ext = Path.GetExtension(filePath);
        if (string.IsNullOrEmpty(ext))
            return null;

        return LanguageExtensions.GetValueOrDefault(ext);
    }

    /// <summary>Get all registered language names.</summary>
    public static IReadOnlyList<string> GetAllLanguages() =>
        Registry.Keys.ToList().AsReadOnly();

    /// <summary>Apply extra extension mappings from environment variable.</summary>
    public static void ApplyExtraExtensions()
    {
        var raw = Environment.GetEnvironmentVariable("JCODEMUNCH_EXTRA_EXTENSIONS")?.Trim();
        if (string.IsNullOrEmpty(raw))
            return;

        foreach (var token in raw.Split(','))
        {
            var trimmed = token.Trim();
            if (string.IsNullOrEmpty(trimmed))
                continue;

            var colonIdx = trimmed.IndexOf(':');
            if (colonIdx < 0)
                continue;

            var ext = trimmed[..colonIdx].Trim();
            var lang = trimmed[(colonIdx + 1)..].Trim();

            if (string.IsNullOrEmpty(ext) || string.IsNullOrEmpty(lang))
                continue;
            if (!Registry.ContainsKey(lang))
                continue;

            LanguageExtensions[ext] = lang;
        }
    }
}
