using System.Text.Json;

namespace JCodeMunch.Mcp.Models;

/// <summary>
/// Index for a repository's source code.
/// </summary>
public sealed class CodeIndex
{
    public const int CurrentIndexVersion = 3;

    /// <summary>"owner/repo"</summary>
    public required string Repo { get; init; }

    public required string Owner { get; init; }
    public required string Name { get; init; }

    /// <summary>ISO timestamp</summary>
    public required string IndexedAt { get; init; }

    /// <summary>All indexed file paths</summary>
    public required List<string> SourceFiles { get; init; }

    /// <summary>Language -> file count</summary>
    public required Dictionary<string, int> Languages { get; init; }

    /// <summary>Serialized Symbol dicts</summary>
    public required List<Dictionary<string, JsonElement>> Symbols { get; init; }

    public int IndexVersion { get; init; } = CurrentIndexVersion;

    /// <summary>file_path -> sha256</summary>
    public Dictionary<string, string> FileHashes { get; init; } = new();

    /// <summary>HEAD commit hash at index time</summary>
    public string GitHead { get; init; } = "";

    /// <summary>file_path -> summary</summary>
    public Dictionary<string, string> FileSummaries { get; init; } = new();

    /// <summary>Find a symbol by ID.</summary>
    public Dictionary<string, JsonElement>? GetSymbol(string symbolId)
    {
        foreach (var sym in Symbols)
        {
            if (sym.TryGetValue("id", out var idElem) && idElem.GetString() == symbolId)
            {
                return sym;
            }
        }

        return null;
    }

    /// <summary>Search symbols with weighted scoring.</summary>
    public List<Dictionary<string, JsonElement>> Search(
        string query,
        string? kind = null,
        string? filePattern = null)
    {
        var queryLower = query.ToLowerInvariant();
        var queryWords = new HashSet<string>(queryLower.Split(' ', StringSplitOptions.RemoveEmptyEntries));

        var scored = new List<(int Score, Dictionary<string, JsonElement> Sym)>();
        foreach (var sym in Symbols)
        {
            // Apply filters
            if (kind is not null && GetStringValue(sym, "kind") != kind)
                continue;
            if (filePattern is not null && !MatchPattern(GetStringValue(sym, "file"), filePattern))
                continue;

            var score = ScoreSymbol(sym, queryLower, queryWords);
            if (score > 0)
            {
                scored.Add((score, sym));
            }
        }

        scored.Sort((a, b) => b.Score.CompareTo(a.Score));
        return scored.Select(s => s.Sym).ToList();
    }

    private static bool MatchPattern(string filePath, string pattern)
    {
        // Simple glob matching using FileSystemName
        return FileSystemName.MatchesSimpleExpression(pattern, filePath, ignoreCase: true)
               || FileSystemName.MatchesSimpleExpression($"*/{pattern}", filePath, ignoreCase: true);
    }

    private static int ScoreSymbol(
        Dictionary<string, JsonElement> sym,
        string queryLower,
        HashSet<string> queryWords)
    {
        var score = 0;

        // 1. Exact name match (highest weight)
        var nameLower = GetStringValue(sym, "name").ToLowerInvariant();
        if (queryLower == nameLower)
            score += 20;
        else if (nameLower.Contains(queryLower, StringComparison.Ordinal))
            score += 10;

        // 2. Name word overlap
        foreach (var word in queryWords)
        {
            if (nameLower.Contains(word, StringComparison.Ordinal))
                score += 5;
        }

        // 3. Signature match
        var sigLower = GetStringValue(sym, "signature").ToLowerInvariant();
        if (sigLower.Contains(queryLower, StringComparison.Ordinal))
            score += 8;
        foreach (var word in queryWords)
        {
            if (sigLower.Contains(word, StringComparison.Ordinal))
                score += 2;
        }

        // 4. Summary match
        var summaryLower = GetStringValue(sym, "summary").ToLowerInvariant();
        if (summaryLower.Contains(queryLower, StringComparison.Ordinal))
            score += 5;
        foreach (var word in queryWords)
        {
            if (summaryLower.Contains(word, StringComparison.Ordinal))
                score += 1;
        }

        // 5. Keyword match
        var keywords = GetStringListValue(sym, "keywords");
        var keywordSet = new HashSet<string>(keywords, StringComparer.OrdinalIgnoreCase);
        foreach (var word in queryWords)
        {
            if (keywordSet.Contains(word))
                score += 3;
        }

        // 6. Docstring match
        var docLower = GetStringValue(sym, "docstring").ToLowerInvariant();
        foreach (var word in queryWords)
        {
            if (docLower.Contains(word, StringComparison.Ordinal))
                score += 1;
        }

        return score;
    }

    private static string GetStringValue(Dictionary<string, JsonElement> sym, string key)
    {
        return sym.TryGetValue(key, out var elem) && elem.ValueKind == JsonValueKind.String
            ? elem.GetString() ?? ""
            : "";
    }

    private static List<string> GetStringListValue(Dictionary<string, JsonElement> sym, string key)
    {
        if (!sym.TryGetValue(key, out var elem) || elem.ValueKind != JsonValueKind.Array)
            return [];

        var result = new List<string>();
        foreach (var item in elem.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                result.Add(item.GetString() ?? "");
            }
        }

        return result;
    }
}

/// <summary>
/// Provides IO.FileSystemName.MatchesSimpleExpression for glob matching.
/// </summary>
internal static class FileSystemName
{
    /// <summary>
    /// Simple glob match supporting * and ? wildcards.
    /// </summary>
    public static bool MatchesSimpleExpression(string pattern, string name, bool ignoreCase = false)
    {
        var comparison = ignoreCase ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        return MatchWildcard(pattern, name, comparison);
    }

    private static bool MatchWildcard(string pattern, string text, StringComparison comparison)
    {
        var pIdx = 0;
        var tIdx = 0;
        var starPIdx = -1;
        var starTIdx = -1;

        while (tIdx < text.Length)
        {
            if (pIdx < pattern.Length && (pattern[pIdx] == '?' ||
                                          string.Compare(pattern, pIdx, text, tIdx, 1, comparison) == 0))
            {
                pIdx++;
                tIdx++;
            }
            else if (pIdx < pattern.Length && pattern[pIdx] == '*')
            {
                starPIdx = pIdx;
                starTIdx = tIdx;
                pIdx++;
            }
            else if (starPIdx >= 0)
            {
                pIdx = starPIdx + 1;
                starTIdx++;
                tIdx = starTIdx;
            }
            else
            {
                return false;
            }
        }

        while (pIdx < pattern.Length && pattern[pIdx] == '*')
            pIdx++;

        return pIdx == pattern.Length;
    }
}
