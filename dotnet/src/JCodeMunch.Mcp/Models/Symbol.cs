using System.Security.Cryptography;
using System.Text;

namespace JCodeMunch.Mcp.Models;

/// <summary>
/// A code symbol extracted from source via tree-sitter.
/// </summary>
public sealed record Symbol
{
    /// <summary>Unique ID: "file_path::QualifiedName#kind"</summary>
    public required string Id { get; init; }

    /// <summary>Source file path (e.g., "src/main.py")</summary>
    public required string File { get; init; }

    /// <summary>Symbol name (e.g., "login")</summary>
    public required string Name { get; init; }

    /// <summary>Fully qualified name (e.g., "MyClass.login")</summary>
    public required string QualifiedName { get; init; }

    /// <summary>"function" | "class" | "method" | "constant" | "type"</summary>
    public required string Kind { get; init; }

    /// <summary>Language identifier (e.g., "python", "csharp")</summary>
    public required string Language { get; init; }

    /// <summary>Full signature line(s)</summary>
    public required string Signature { get; init; }

    /// <summary>Extracted docstring (language-specific)</summary>
    public string Docstring { get; init; } = "";

    /// <summary>One-line summary</summary>
    public string Summary { get; set; } = "";

    /// <summary>Decorators/attributes</summary>
    public List<string> Decorators { get; init; } = [];

    /// <summary>Extracted search keywords</summary>
    public List<string> Keywords { get; init; } = [];

    /// <summary>Parent symbol ID (for methods -> class)</summary>
    public string? Parent { get; init; }

    /// <summary>Start line number (1-indexed)</summary>
    public int Line { get; init; }

    /// <summary>End line number (1-indexed)</summary>
    public int EndLine { get; init; }

    /// <summary>Start byte in raw file</summary>
    public int ByteOffset { get; init; }

    /// <summary>Byte length of full source</summary>
    public int ByteLength { get; init; }

    /// <summary>SHA-256 of symbol source bytes (for drift detection)</summary>
    public string ContentHash { get; init; } = "";

    /// <summary>
    /// Generate unique symbol ID.
    /// Format: {filePath}::{qualifiedName}#{kind}
    /// </summary>
    public static string MakeSymbolId(string filePath, string qualifiedName, string kind = "")
    {
        return string.IsNullOrEmpty(kind)
            ? $"{filePath}::{qualifiedName}"
            : $"{filePath}::{qualifiedName}#{kind}";
    }

    /// <summary>
    /// Compute SHA-256 hash of symbol source bytes for drift detection.
    /// </summary>
    public static string ComputeContentHash(byte[] sourceBytes)
    {
        var hash = SHA256.HashData(sourceBytes);
        return Convert.ToHexStringLower(hash);
    }
}
