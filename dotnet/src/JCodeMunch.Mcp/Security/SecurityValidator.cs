namespace JCodeMunch.Mcp.Security;

/// <summary>
/// Security utilities for path validation, secret detection, and binary filtering.
/// Port of Python security.py.
/// </summary>
public static class SecurityValidator
{
    public const int DefaultMaxFileSize = 500 * 1024; // 500KB
    public const int DefaultMaxIndexFiles = 10_000;
    public const string MaxIndexFilesEnvVar = "JCODEMUNCH_MAX_INDEX_FILES";

    // --- Secret File Detection ---

    private static readonly string[] SecretPatterns =
    [
        "*.env",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.credentials",
        "*.keystore",
        "*.jks",
        "*.token",
        "*secret*",
        "id_rsa",
        "id_rsa.*",
        "id_ed25519",
        "id_ed25519.*",
        "id_dsa",
        "id_ecdsa",
        ".htpasswd",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "service-account*.json",
        "*.secrets",
    ];

    // --- Binary File Detection ---

    private static readonly HashSet<string> BinaryExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        // Executables
        ".exe", ".dll", ".so", ".dylib", ".bin", ".out",
        // Object files
        ".o", ".obj", ".a", ".lib",
        // Archives
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        // Images
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
        ".webp", ".tiff", ".tif",
        // Media
        ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
        ".ogg", ".webm",
        // Documents
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        // Compiled / bytecode
        ".pyc", ".pyo", ".class", ".wasm",
        // Database
        ".db", ".sqlite", ".sqlite3",
        // Fonts
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        // Other
        ".jar", ".war", ".ear",
    };

    /// <summary>
    /// Check that target path resolves within root directory.
    /// Prevents path traversal attacks and symlink escapes.
    /// </summary>
    public static bool ValidatePath(string root, string target)
    {
        try
        {
            var resolvedRoot = Path.GetFullPath(root);
            var resolvedTarget = Path.GetFullPath(target);
            return resolvedTarget.StartsWith(resolvedRoot + Path.DirectorySeparatorChar, StringComparison.Ordinal)
                   || resolvedTarget == resolvedRoot;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Check if a symlink points outside the root directory.
    /// </summary>
    public static bool ValidateSymlinks(string path, string root)
    {
        try
        {
            var info = new FileInfo(path);
            if (info.LinkTarget is not null)
            {
                var resolvedRoot = Path.GetFullPath(root);
                var resolvedTarget = Path.GetFullPath(
                    Path.IsPathRooted(info.LinkTarget)
                        ? info.LinkTarget
                        : Path.Combine(Path.GetDirectoryName(path)!, info.LinkTarget));

                return resolvedTarget.StartsWith(resolvedRoot + Path.DirectorySeparatorChar, StringComparison.Ordinal)
                       || resolvedTarget == resolvedRoot;
            }

            return true; // Not a symlink
        }
        catch
        {
            return false; // Can't resolve -> treat as escape
        }
    }

    /// <summary>
    /// Check if a file path matches known secret file patterns.
    /// </summary>
    public static bool IsSecretFile(string filePath)
    {
        var name = Path.GetFileName(filePath).ToLowerInvariant();
        var pathLower = filePath.ToLowerInvariant();

        foreach (var pattern in SecretPatterns)
        {
            if (SimpleGlobMatch(pattern, name) || SimpleGlobMatch(pattern, pathLower))
                return true;
        }

        return false;
    }

    /// <summary>
    /// Check if a file has a known binary extension.
    /// </summary>
    public static bool IsBinaryExtension(string filePath)
    {
        var ext = Path.GetExtension(filePath);
        return !string.IsNullOrEmpty(ext) && BinaryExtensions.Contains(ext);
    }

    /// <summary>
    /// Detect binary content by checking for null bytes.
    /// </summary>
    public static bool IsBinaryContent(byte[] data, int checkSize = 8192)
    {
        var limit = Math.Min(data.Length, checkSize);
        for (var i = 0; i < limit; i++)
        {
            if (data[i] == 0)
                return true;
        }

        return false;
    }

    /// <summary>
    /// Check if a file is binary using extension check + content sniffing.
    /// </summary>
    public static bool IsBinaryFile(string filePath, int checkSize = 8192)
    {
        if (IsBinaryExtension(filePath))
            return true;

        try
        {
            using var fs = File.OpenRead(filePath);
            var buffer = new byte[Math.Min(checkSize, fs.Length)];
            _ = fs.Read(buffer, 0, buffer.Length);
            return IsBinaryContent(buffer, checkSize);
        }
        catch
        {
            return true; // Can't read -> skip
        }
    }

    /// <summary>
    /// Resolve the maximum indexed file count from argument or environment.
    /// </summary>
    public static int GetMaxIndexFiles(int? maxFiles = null)
    {
        if (maxFiles is not null)
        {
            if (maxFiles.Value <= 0)
                throw new ArgumentException("maxFiles must be a positive integer");
            return maxFiles.Value;
        }

        var envValue = Environment.GetEnvironmentVariable(MaxIndexFilesEnvVar);
        if (envValue is null)
            return DefaultMaxIndexFiles;

        return int.TryParse(envValue, out var parsed) && parsed > 0
            ? parsed
            : DefaultMaxIndexFiles;
    }

    /// <summary>
    /// Run all security checks on a file. Returns reason string if excluded, null if ok.
    /// </summary>
    public static string? ShouldExcludeFile(
        string filePath,
        string root,
        int maxFileSize = DefaultMaxFileSize,
        bool checkSecrets = true,
        bool checkBinary = true,
        bool checkSymlinks = true)
    {
        // Symlink escape
        if (checkSymlinks && !ValidateSymlinks(filePath, root))
            return "symlink_escape";

        // Path traversal
        if (!ValidatePath(root, filePath))
            return "path_traversal";

        // Get relative path
        string relPath;
        try
        {
            relPath = Path.GetRelativePath(root, filePath).Replace('\\', '/');
        }
        catch
        {
            return "outside_root";
        }

        // Secret detection
        if (checkSecrets && IsSecretFile(relPath))
            return "secret_file";

        // File size
        try
        {
            var size = new FileInfo(filePath).Length;
            if (size > maxFileSize)
                return "file_too_large";
        }
        catch
        {
            return "unreadable";
        }

        // Binary detection (extension only for fast path)
        if (checkBinary && IsBinaryExtension(relPath))
            return "binary_extension";

        return null;
    }

    /// <summary>
    /// Decode bytes to string with replacement for invalid sequences.
    /// </summary>
    public static string SafeDecode(byte[] data)
    {
        return System.Text.Encoding.UTF8.GetString(data);
    }

    // --- Private helpers ---

    /// <summary>
    /// Simple glob match supporting * and ? wildcards (case-insensitive).
    /// </summary>
    private static bool SimpleGlobMatch(string pattern, string text)
    {
        var pIdx = 0;
        var tIdx = 0;
        var starPIdx = -1;
        var starTIdx = -1;

        while (tIdx < text.Length)
        {
            if (pIdx < pattern.Length && (pattern[pIdx] == '?' ||
                                          char.ToLowerInvariant(pattern[pIdx]) == char.ToLowerInvariant(text[tIdx])))
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
