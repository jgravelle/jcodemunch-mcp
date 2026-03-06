using ModelContextProtocol.Server;
using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;
using JCodeMunch.Mcp.Storage;

namespace JCodeMunch.Mcp.Tools;

[McpServerToolType]
public static class ListReposTool
{
    [McpServerTool(Name = "list_repos"), Description("List all indexed repositories.")]
    public static string ListRepos(IndexStore store)
    {
        var sw = Stopwatch.StartNew();
        var repos = store.ListRepos();
        sw.Stop();

        var result = new Dictionary<string, object>
        {
            ["count"] = repos.Count,
            ["repos"] = repos,
            ["_meta"] = new Dictionary<string, object>
            {
                ["timing_ms"] = Math.Round(sw.Elapsed.TotalMilliseconds, 1),
            },
        };

        return JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
    }
}
