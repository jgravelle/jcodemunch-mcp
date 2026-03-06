using JCodeMunch.Mcp.Storage;

namespace JCodeMunch.Mcp.Tools;

/// <summary>
/// Shared helpers for tool modules.
/// Port of Python tools/_utils.py.
/// </summary>
internal static class ToolUtils
{
    /// <summary>
    /// Parse "owner/repo" or look up a single repo name.
    /// Returns (Owner, Name).
    /// </summary>
    /// <exception cref="ArgumentException">Thrown when the repository is not found.</exception>
    public static (string Owner, string Name) ResolveRepo(string repo, IndexStore store)
    {
        if (repo.Contains('/'))
        {
            var parts = repo.Split('/', 2);
            return (parts[0], parts[1]);
        }

        var repos = store.ListRepos();
        var matching = repos
            .Where(r => r.TryGetValue("repo", out var repoVal)
                        && repoVal is string repoStr
                        && repoStr.EndsWith($"/{repo}", StringComparison.Ordinal))
            .ToList();

        if (matching.Count == 0)
            throw new ArgumentException($"Repository not found: {repo}");

        var fullRepo = (string)matching[0]["repo"];
        var repoParts = fullRepo.Split('/', 2);
        return (repoParts[0], repoParts[1]);
    }

    /// <summary>
    /// Compute the content directory path for a repository.
    /// Mirrors IndexStore.ContentDir: basePath/{owner}-{name}
    /// </summary>
    public static string GetContentDir(string? storagePath, string owner, string name)
    {
        var basePath = storagePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".code-index");

        return Path.Combine(basePath, $"{owner}-{name}");
    }
}
