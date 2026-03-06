using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;
using JCodeMunch.Mcp.Storage;
using ModelContextProtocol.Server;

namespace JCodeMunch.Mcp.Tools;

/// <summary>
/// MCP tool: get_repo_outline
/// Returns a high-level overview of an indexed repository.
/// Port of Python tools/get_repo_outline.py.
/// </summary>
[McpServerToolType]
public static class GetRepoOutlineTool
{
    [McpServerTool(Name = "get_repo_outline"), Description("Get a high-level overview of an indexed repository.")]
    public static string GetRepoOutline(
        IndexStore store,
        TokenTracker tracker,
        [Description("Repository identifier (owner/repo or repo name)")] string repo)
    {
        var sw = Stopwatch.StartNew();

        string owner, name;
        try
        {
            (owner, name) = ToolUtils.ResolveRepo(repo, store);
        }
        catch (ArgumentException ex)
        {
            return JsonSerializer.Serialize(new { error = ex.Message });
        }

        var index = store.LoadIndex(owner, name);
        if (index is null)
            return JsonSerializer.Serialize(new { error = $"Repository not indexed: {owner}/{name}" });

        // Directory-level file counts
        var dirCounts = new Dictionary<string, int>();
        foreach (var file in index.SourceFiles)
        {
            var parts = file.Split('/');
            var dir = parts.Length > 1 ? parts[0] + "/" : "(root)";
            dirCounts[dir] = dirCounts.GetValueOrDefault(dir) + 1;
        }

        var sortedDirs = dirCounts
            .OrderByDescending(kv => kv.Value)
            .ToDictionary(kv => kv.Key, kv => kv.Value);

        // Symbol kind breakdown
        var kindCounts = new Dictionary<string, int>();
        foreach (var sym in index.Symbols)
        {
            var kind = sym.TryGetValue("kind", out var k) && k.ValueKind == JsonValueKind.String
                ? k.GetString() ?? "unknown"
                : "unknown";
            kindCounts[kind] = kindCounts.GetValueOrDefault(kind) + 1;
        }

        var sortedKinds = kindCounts
            .OrderByDescending(kv => kv.Value)
            .ToDictionary(kv => kv.Key, kv => kv.Value);

        // Token savings: sum of all raw file sizes
        var storagePath = Environment.GetEnvironmentVariable("CODE_INDEX_PATH");
        var contentDir = ToolUtils.GetContentDir(storagePath, owner, name);

        var rawBytes = 0L;
        var contentDirFull = Path.GetFullPath(contentDir);
        foreach (var file in index.SourceFiles)
        {
            try
            {
                var fullPath = Path.GetFullPath(Path.Combine(contentDir, file));
                if (fullPath.StartsWith(contentDirFull, StringComparison.Ordinal)
                    && File.Exists(fullPath))
                {
                    rawBytes += new FileInfo(fullPath).Length;
                }
            }
            catch
            {
                // Skip inaccessible files
            }
        }

        var tokensSaved = TokenTracker.EstimateSavings((int)Math.Min(rawBytes, int.MaxValue), 0);
        var totalSaved = tracker.RecordSaving(tokensSaved);

        var elapsedMs = Math.Round(sw.Elapsed.TotalMilliseconds, 1);

        var costAvoided = TokenTracker.CostAvoided(tokensSaved, totalSaved);

        var meta = new Dictionary<string, object>
        {
            ["timing_ms"] = elapsedMs,
            ["tokens_saved"] = tokensSaved,
            ["total_tokens_saved"] = totalSaved,
        };
        foreach (var (key, value) in costAvoided)
            meta[key] = value;

        var result = new Dictionary<string, object>
        {
            ["repo"] = $"{owner}/{name}",
            ["indexed_at"] = index.IndexedAt,
            ["file_count"] = index.SourceFiles.Count,
            ["symbol_count"] = index.Symbols.Count,
            ["languages"] = index.Languages,
            ["directories"] = sortedDirs,
            ["symbol_kinds"] = sortedKinds,
            ["_meta"] = meta,
        };

        return JsonSerializer.Serialize(result);
    }
}
