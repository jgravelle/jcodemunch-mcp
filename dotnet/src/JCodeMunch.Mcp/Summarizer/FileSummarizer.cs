using JCodeMunch.Mcp.Models;

namespace JCodeMunch.Mcp.Summarizer;

/// <summary>
/// Generate per-file heuristic summaries from symbol information.
/// Port of Python summarizer/file_summarize.py.
/// </summary>
public static class FileSummarizer
{
    /// <summary>
    /// Generate heuristic summaries for each file from symbol data.
    /// </summary>
    /// <param name="fileSymbols">Maps file path -> list of Symbol objects for that file</param>
    /// <returns>Dict mapping file path -> summary string</returns>
    public static Dictionary<string, string> GenerateFileSummaries(
        Dictionary<string, List<Symbol>> fileSymbols)
    {
        var summaries = new Dictionary<string, string>();

        foreach (var (filePath, symbols) in fileSymbols)
        {
            summaries[filePath] = HeuristicSummary(filePath, symbols);
        }

        return summaries;
    }

    /// <summary>Generate summary from symbol information.</summary>
    private static string HeuristicSummary(string filePath, List<Symbol> symbols)
    {
        if (symbols.Count == 0)
            return "";

        var classes = symbols.Where(s => s.Kind == "class").ToList();
        var functions = symbols.Where(s => s.Kind == "function").ToList();
        var methods = symbols.Where(s => s.Kind == "method").ToList();
        var constants = symbols.Where(s => s.Kind == "constant").ToList();
        var types = symbols.Where(s => s.Kind == "type").ToList();

        var parts = new List<string>();

        if (classes.Count > 0)
        {
            foreach (var cls in classes.Take(2))
            {
                var methodCount = symbols.Count(s =>
                    s.Parent is not null && s.Parent.EndsWith($"::{cls.Name}#class", StringComparison.Ordinal));
                parts.Add($"Defines {cls.Name} class ({methodCount} methods)");
            }
        }

        if (functions.Count > 0)
        {
            if (functions.Count <= 3)
            {
                var names = string.Join(", ", functions.Select(f => f.Name));
                parts.Add($"Contains {functions.Count} functions: {names}");
            }
            else
            {
                var names = string.Join(", ", functions.Take(3).Select(f => f.Name));
                parts.Add($"Contains {functions.Count} functions: {names}, ...");
            }
        }

        if (types.Count > 0 && parts.Count == 0)
        {
            var names = string.Join(", ", types.Take(3).Select(t => t.Name));
            parts.Add($"Defines types: {names}");
        }

        if (constants.Count > 0 && parts.Count == 0)
        {
            parts.Add($"Defines {constants.Count} constants");
        }

        return parts.Count > 0 ? string.Join(". ", parts) : "";
    }
}
