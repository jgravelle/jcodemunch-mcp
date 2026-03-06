using JCodeMunch.Mcp.Models;

namespace JCodeMunch.Mcp.Parser;

/// <summary>
/// Generic AST symbol extractor using tree-sitter.
/// Port of Python parser/extractor.py.
///
/// NOTE: The actual tree-sitter parsing logic will be implemented when
/// TreeSitter.Bindings is integrated. This class provides the interface
/// and data flow that mirrors the Python implementation.
/// </summary>
public sealed class SymbolExtractor
{
    /// <summary>
    /// Parse source code and extract symbols using tree-sitter.
    /// </summary>
    /// <param name="content">Raw source code</param>
    /// <param name="filePath">File path (for ID generation)</param>
    /// <param name="language">Language name (must be in LanguageRegistry)</param>
    /// <returns>List of Symbol objects</returns>
    public List<Symbol> ExtractSymbols(string content, string filePath, string language)
    {
        if (!LanguageRegistry.Registry.ContainsKey(language))
            return [];

        var sourceBytes = System.Text.Encoding.UTF8.GetBytes(content);

        List<Symbol> symbols;
        if (language == "cpp")
        {
            symbols = ParseCppSymbols(sourceBytes, filePath);
        }
        else if (language == "elixir")
        {
            symbols = ParseElixirSymbols(sourceBytes, filePath);
        }
        else
        {
            var spec = LanguageRegistry.Registry[language];
            symbols = ParseWithSpec(sourceBytes, filePath, language, spec);
        }

        // Disambiguate overloaded symbols (same ID)
        symbols = DisambiguateOverloads(symbols);

        return symbols;
    }

    /// <summary>Parse source bytes using one language spec.</summary>
    private List<Symbol> ParseWithSpec(
        byte[] sourceBytes,
        string filename,
        string language,
        LanguageSpec spec)
    {
        // TODO: Integrate with TreeSitter.Bindings to perform actual parsing.
        // The tree-sitter integration will:
        // 1. Create a parser for spec.TsLanguage
        // 2. Parse sourceBytes into an AST
        // 3. Walk the AST using spec.SymbolNodeTypes to find symbols
        // 4. Extract names via spec.NameFields
        // 5. Build signatures from spec.ParamFields and spec.ReturnTypeFields
        // 6. Extract docstrings based on spec.DocstringStrategy
        // 7. Extract decorators from spec.DecoratorNodeType
        // 8. Handle nesting via spec.ContainerNodeTypes
        // 9. Extract constants from spec.ConstantPatterns
        // 10. Compute byte offsets and content hashes

        return [];
    }

    /// <summary>Parse C++ with auto-fallback to C for .h files.</summary>
    private List<Symbol> ParseCppSymbols(byte[] sourceBytes, string filename)
    {
        // TODO: Implement C++ parsing with C fallback for .h files.
        // See Python _parse_cpp_symbols for the dual-parse strategy.
        return [];
    }

    /// <summary>Parse Elixir using custom extraction (homoiconic grammar).</summary>
    private List<Symbol> ParseElixirSymbols(byte[] sourceBytes, string filename)
    {
        // TODO: Implement Elixir-specific extraction.
        // Elixir's tree-sitter grammar uses generic call nodes.
        return [];
    }

    /// <summary>
    /// Disambiguate overloaded symbols by appending numeric suffixes.
    /// </summary>
    private static List<Symbol> DisambiguateOverloads(List<Symbol> symbols)
    {
        var idCounts = new Dictionary<string, int>();
        var result = new List<Symbol>(symbols.Count);

        foreach (var sym in symbols)
        {
            if (idCounts.TryGetValue(sym.Id, out var count))
            {
                idCounts[sym.Id] = count + 1;
                // Create a new symbol with disambiguated ID
                result.Add(sym with
                {
                    Id = $"{sym.Id}#{count + 1}",
                    QualifiedName = $"{sym.QualifiedName}#{count + 1}",
                });
            }
            else
            {
                idCounts[sym.Id] = 1;
                result.Add(sym);
            }
        }

        return result;
    }
}
