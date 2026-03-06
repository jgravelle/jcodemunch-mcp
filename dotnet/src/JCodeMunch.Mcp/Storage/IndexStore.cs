using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using JCodeMunch.Mcp.Models;

namespace JCodeMunch.Mcp.Storage;

/// <summary>
/// Storage for code indexes with byte-offset content retrieval.
/// Port of Python storage/index_store.py (IndexStore class).
/// </summary>
public sealed partial class IndexStore
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    private readonly string _basePath;

    /// <summary>
    /// Initialize store.
    /// </summary>
    /// <param name="basePath">Base directory for storage. Defaults to ~/.code-index/</param>
    public IndexStore(string? basePath = null)
    {
        _basePath = basePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".code-index");

        Directory.CreateDirectory(_basePath);
    }

    /// <summary>Save index and raw files to storage.</summary>
    public CodeIndex SaveIndex(
        string owner,
        string name,
        List<string> sourceFiles,
        List<Symbol> symbols,
        Dictionary<string, string> rawFiles,
        Dictionary<string, int> languages,
        Dictionary<string, string>? fileHashes = null,
        string gitHead = "",
        Dictionary<string, string>? fileSummaries = null)
    {
        fileHashes ??= rawFiles.ToDictionary(kv => kv.Key, kv => FileHash(kv.Value));

        var index = new CodeIndex
        {
            Repo = $"{owner}/{name}",
            Owner = owner,
            Name = name,
            IndexedAt = DateTime.UtcNow.ToString("o"),
            SourceFiles = sourceFiles,
            Languages = languages,
            Symbols = symbols.Select(SymbolToDict).ToList(),
            IndexVersion = CodeIndex.CurrentIndexVersion,
            FileHashes = fileHashes,
            GitHead = gitHead,
            FileSummaries = fileSummaries ?? new Dictionary<string, string>(),
        };

        // Save index JSON atomically: write to temp then rename
        var indexPath = IndexPath(owner, name);
        var tmpPath = indexPath + ".tmp";
        var json = JsonSerializer.Serialize(IndexToSerializable(index), JsonOptions);
        File.WriteAllText(tmpPath, json, Encoding.UTF8);
        File.Move(tmpPath, indexPath, overwrite: true);

        // Save raw files
        var contentDir = ContentDir(owner, name);
        Directory.CreateDirectory(contentDir);

        foreach (var (filePath, content) in rawFiles)
        {
            var dest = SafeContentPath(contentDir, filePath);
            if (dest is null)
                throw new ArgumentException($"Unsafe file path in rawFiles: {filePath}");

            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.WriteAllText(dest, content, Encoding.UTF8);
        }

        return index;
    }

    /// <summary>Load index from storage. Rejects incompatible versions.</summary>
    public CodeIndex? LoadIndex(string owner, string name)
    {
        var indexPath = IndexPath(owner, name);
        if (!File.Exists(indexPath))
            return null;

        var json = File.ReadAllText(indexPath, Encoding.UTF8);
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var storedVersion = root.TryGetProperty("index_version", out var vElem) ? vElem.GetInt32() : 1;
        if (storedVersion > CodeIndex.CurrentIndexVersion)
            return null; // Future version we can't read

        return new CodeIndex
        {
            Repo = root.GetProperty("repo").GetString()!,
            Owner = root.GetProperty("owner").GetString()!,
            Name = root.GetProperty("name").GetString()!,
            IndexedAt = root.GetProperty("indexed_at").GetString()!,
            SourceFiles = DeserializeStringList(root.GetProperty("source_files")),
            Languages = DeserializeIntDict(root.GetProperty("languages")),
            Symbols = DeserializeSymbolList(root.GetProperty("symbols")),
            IndexVersion = storedVersion,
            FileHashes = root.TryGetProperty("file_hashes", out var fh) ? DeserializeStringDict(fh) : new(),
            GitHead = root.TryGetProperty("git_head", out var gh) ? gh.GetString() ?? "" : "",
            FileSummaries = root.TryGetProperty("file_summaries", out var fs) ? DeserializeStringDict(fs) : new(),
        };
    }

    /// <summary>Read symbol source using stored byte offsets.</summary>
    public string? GetSymbolContent(string owner, string name, string symbolId)
    {
        var index = LoadIndex(owner, name);
        if (index is null) return null;

        var symbol = index.GetSymbol(symbolId);
        if (symbol is null) return null;

        var file = symbol.TryGetValue("file", out var fElem) ? fElem.GetString() : null;
        if (file is null) return null;

        var filePath = SafeContentPath(ContentDir(owner, name), file);
        if (filePath is null || !File.Exists(filePath)) return null;

        var byteOffset = symbol.TryGetValue("byte_offset", out var bo) ? bo.GetInt32() : 0;
        var byteLength = symbol.TryGetValue("byte_length", out var bl) ? bl.GetInt32() : 0;

        using var fs = File.OpenRead(filePath);
        fs.Seek(byteOffset, SeekOrigin.Begin);
        var buffer = new byte[byteLength];
        _ = fs.Read(buffer, 0, byteLength);
        return Encoding.UTF8.GetString(buffer);
    }

    /// <summary>Detect changed, new, and deleted files by comparing hashes.</summary>
    public (List<string> Changed, List<string> New, List<string> Deleted) DetectChanges(
        string owner,
        string name,
        Dictionary<string, string> currentFiles)
    {
        var index = LoadIndex(owner, name);
        if (index is null)
            return ([], new List<string>(currentFiles.Keys), []);

        var oldHashes = index.FileHashes;
        var currentHashes = currentFiles.ToDictionary(kv => kv.Key, kv => FileHash(kv.Value));

        var oldSet = new HashSet<string>(oldHashes.Keys);
        var newSet = new HashSet<string>(currentHashes.Keys);

        var newFiles = newSet.Except(oldSet).ToList();
        var deletedFiles = oldSet.Except(newSet).ToList();
        var changedFiles = oldSet.Intersect(newSet)
            .Where(fp => oldHashes[fp] != currentHashes[fp])
            .ToList();

        return (changedFiles, newFiles, deletedFiles);
    }

    /// <summary>Incrementally update an existing index.</summary>
    public CodeIndex? IncrementalSave(
        string owner,
        string name,
        List<string> changedFiles,
        List<string> newFiles,
        List<string> deletedFiles,
        List<Symbol> newSymbols,
        Dictionary<string, string> rawFiles,
        Dictionary<string, int> languages,
        string gitHead = "",
        Dictionary<string, string>? fileSummaries = null)
    {
        var index = LoadIndex(owner, name);
        if (index is null) return null;

        // Remove symbols for deleted and changed files
        var filesToRemove = new HashSet<string>(deletedFiles.Concat(changedFiles));
        var keptSymbols = index.Symbols
            .Where(s => !s.TryGetValue("file", out var f) || !filesToRemove.Contains(f.GetString() ?? ""))
            .ToList();

        // Add new symbols
        var allSymbols = keptSymbols.Concat(newSymbols.Select(SymbolToDict)).ToList();
        var recomputedLanguages = LanguagesFromSymbols(allSymbols);
        if (recomputedLanguages.Count == 0 && languages.Count > 0)
            recomputedLanguages = languages;

        // Update source files
        var oldFiles = new HashSet<string>(index.SourceFiles);
        foreach (var f in deletedFiles) oldFiles.Remove(f);
        foreach (var f in newFiles) oldFiles.Add(f);
        foreach (var f in changedFiles) oldFiles.Add(f);

        // Update file hashes
        var fileHashes = new Dictionary<string, string>(index.FileHashes);
        foreach (var f in deletedFiles) fileHashes.Remove(f);
        foreach (var (fp, content) in rawFiles) fileHashes[fp] = FileHash(content);

        // Merge file summaries
        var mergedSummaries = new Dictionary<string, string>(index.FileSummaries);
        foreach (var f in deletedFiles) mergedSummaries.Remove(f);
        if (fileSummaries is not null)
        {
            foreach (var (k, v) in fileSummaries) mergedSummaries[k] = v;
        }

        var updated = new CodeIndex
        {
            Repo = $"{owner}/{name}",
            Owner = owner,
            Name = name,
            IndexedAt = DateTime.UtcNow.ToString("o"),
            SourceFiles = oldFiles.Order().ToList(),
            Languages = recomputedLanguages,
            Symbols = allSymbols,
            IndexVersion = CodeIndex.CurrentIndexVersion,
            FileHashes = fileHashes,
            GitHead = gitHead,
            FileSummaries = mergedSummaries,
        };

        // Save atomically
        var indexPath = IndexPath(owner, name);
        var tmpPath = indexPath + ".tmp";
        var json = JsonSerializer.Serialize(IndexToSerializable(updated), JsonOptions);
        File.WriteAllText(tmpPath, json, Encoding.UTF8);
        File.Move(tmpPath, indexPath, overwrite: true);

        // Update raw files
        var contentDir = ContentDir(owner, name);
        Directory.CreateDirectory(contentDir);

        foreach (var fp in deletedFiles)
        {
            var dead = SafeContentPath(contentDir, fp);
            if (dead is not null && File.Exists(dead))
                File.Delete(dead);
        }

        foreach (var (fp, content) in rawFiles)
        {
            var dest = SafeContentPath(contentDir, fp);
            if (dest is null)
                throw new ArgumentException($"Unsafe file path in rawFiles: {fp}");
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.WriteAllText(dest, content, Encoding.UTF8);
        }

        return updated;
    }

    /// <summary>List all indexed repositories.</summary>
    public List<Dictionary<string, object>> ListRepos()
    {
        var repos = new List<Dictionary<string, object>>();
        foreach (var indexFile in Directory.EnumerateFiles(_basePath, "*.json"))
        {
            try
            {
                var json = File.ReadAllText(indexFile, Encoding.UTF8);
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                repos.Add(new Dictionary<string, object>
                {
                    ["repo"] = root.GetProperty("repo").GetString()!,
                    ["indexed_at"] = root.GetProperty("indexed_at").GetString()!,
                    ["symbol_count"] = root.GetProperty("symbols").GetArrayLength(),
                    ["file_count"] = root.GetProperty("source_files").GetArrayLength(),
                    ["languages"] = DeserializeIntDict(root.GetProperty("languages")),
                    ["index_version"] = root.TryGetProperty("index_version", out var v) ? v.GetInt32() : 1,
                });
            }
            catch
            {
                // Skip invalid files
            }
        }

        return repos;
    }

    /// <summary>Delete an index and its raw files.</summary>
    public bool DeleteIndex(string owner, string name)
    {
        var indexPath = IndexPath(owner, name);
        var contentDir = ContentDir(owner, name);
        var deleted = false;

        if (File.Exists(indexPath))
        {
            File.Delete(indexPath);
            deleted = true;
        }

        if (Directory.Exists(contentDir))
        {
            Directory.Delete(contentDir, recursive: true);
            deleted = true;
        }

        return deleted;
    }

    /// <summary>Get current HEAD commit hash for a git repo, or null.</summary>
    public static string? GetGitHead(string repoPath)
    {
        try
        {
            var psi = new ProcessStartInfo("git", "rev-parse HEAD")
            {
                WorkingDirectory = repoPath,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var process = Process.Start(psi);
            if (process is null) return null;
            var output = process.StandardOutput.ReadToEnd().Trim();
            process.WaitForExit(5000);
            return process.ExitCode == 0 ? output : null;
        }
        catch
        {
            return null;
        }
    }

    // --- Private helpers ---

    [GeneratedRegex(@"^[A-Za-z0-9._-]+$")]
    private static partial Regex SafeComponentRegex();

    private static string SafeRepoComponent(string value, string fieldName)
    {
        if (string.IsNullOrEmpty(value) || value is "." or "..")
            throw new ArgumentException($"Invalid {fieldName}: '{value}'");
        if (value.Contains('/') || value.Contains('\\'))
            throw new ArgumentException($"Invalid {fieldName}: '{value}'");
        if (!SafeComponentRegex().IsMatch(value))
            throw new ArgumentException($"Invalid {fieldName}: '{value}'");
        return value;
    }

    private string RepoSlug(string owner, string name)
    {
        var safeOwner = SafeRepoComponent(owner, "owner");
        var safeName = SafeRepoComponent(name, "name");
        return $"{safeOwner}-{safeName}";
    }

    private string IndexPath(string owner, string name) =>
        Path.Combine(_basePath, $"{RepoSlug(owner, name)}.json");

    private string ContentDir(string owner, string name) =>
        Path.Combine(_basePath, RepoSlug(owner, name));

    /// <summary>
    /// Resolve a content path and ensure it stays within contentDir.
    /// Prevents path traversal.
    /// </summary>
    private static string? SafeContentPath(string contentDir, string relativePath)
    {
        try
        {
            var baseFull = Path.GetFullPath(contentDir);
            var candidateFull = Path.GetFullPath(Path.Combine(contentDir, relativePath));

            // Ensure the candidate is inside base directory
            if (!candidateFull.StartsWith(baseFull + Path.DirectorySeparatorChar, StringComparison.Ordinal)
                && candidateFull != baseFull)
            {
                return null;
            }

            return candidateFull;
        }
        catch
        {
            return null;
        }
    }

    private static string FileHash(string content)
    {
        var bytes = Encoding.UTF8.GetBytes(content);
        var hash = SHA256.HashData(bytes);
        return Convert.ToHexStringLower(hash);
    }

    private static Dictionary<string, JsonElement> SymbolToDict(Symbol symbol)
    {
        var json = JsonSerializer.Serialize(new
        {
            id = symbol.Id,
            file = symbol.File,
            name = symbol.Name,
            qualified_name = symbol.QualifiedName,
            kind = symbol.Kind,
            language = symbol.Language,
            signature = symbol.Signature,
            docstring = symbol.Docstring,
            summary = symbol.Summary,
            decorators = symbol.Decorators,
            keywords = symbol.Keywords,
            parent = symbol.Parent,
            line = symbol.Line,
            end_line = symbol.EndLine,
            byte_offset = symbol.ByteOffset,
            byte_length = symbol.ByteLength,
            content_hash = symbol.ContentHash,
        });

        return JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json)!;
    }

    private static object IndexToSerializable(CodeIndex index) => new
    {
        repo = index.Repo,
        owner = index.Owner,
        name = index.Name,
        indexed_at = index.IndexedAt,
        source_files = index.SourceFiles,
        languages = index.Languages,
        symbols = index.Symbols,
        index_version = index.IndexVersion,
        file_hashes = index.FileHashes,
        git_head = index.GitHead,
        file_summaries = index.FileSummaries,
    };

    private static Dictionary<string, int> LanguagesFromSymbols(
        List<Dictionary<string, JsonElement>> symbols)
    {
        var fileLanguages = new Dictionary<string, string>();
        foreach (var sym in symbols)
        {
            var file = sym.TryGetValue("file", out var f) ? f.GetString() : null;
            var lang = sym.TryGetValue("language", out var l) ? l.GetString() : null;
            if (file is not null && lang is not null)
                fileLanguages.TryAdd(file, lang);
        }

        var counts = new Dictionary<string, int>();
        foreach (var lang in fileLanguages.Values)
            counts[lang] = counts.GetValueOrDefault(lang) + 1;

        return counts;
    }

    private static List<string> DeserializeStringList(JsonElement elem)
    {
        var list = new List<string>();
        foreach (var item in elem.EnumerateArray())
            list.Add(item.GetString() ?? "");
        return list;
    }

    private static Dictionary<string, string> DeserializeStringDict(JsonElement elem)
    {
        var dict = new Dictionary<string, string>();
        foreach (var prop in elem.EnumerateObject())
            dict[prop.Name] = prop.Value.GetString() ?? "";
        return dict;
    }

    private static Dictionary<string, int> DeserializeIntDict(JsonElement elem)
    {
        var dict = new Dictionary<string, int>();
        foreach (var prop in elem.EnumerateObject())
            dict[prop.Name] = prop.Value.GetInt32();
        return dict;
    }

    private static List<Dictionary<string, JsonElement>> DeserializeSymbolList(JsonElement elem)
    {
        var list = new List<Dictionary<string, JsonElement>>();
        foreach (var item in elem.EnumerateArray())
        {
            var dict = new Dictionary<string, JsonElement>();
            foreach (var prop in item.EnumerateObject())
                dict[prop.Name] = prop.Value.Clone();
            list.Add(dict);
        }

        return list;
    }
}
