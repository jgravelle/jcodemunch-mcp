using System.Text.Json;

namespace JCodeMunch.Mcp.Storage;

/// <summary>
/// Persistent token savings tracker.
/// Records cumulative tokens saved in ~/.code-index/_savings.json.
/// Port of Python storage/token_tracker.py.
/// </summary>
public sealed class TokenTracker
{
    private const string SavingsFileName = "_savings.json";
    private const int BytesPerToken = 4; // ~4 bytes per token

    /// <summary>Input token pricing ($ per token).</summary>
    public static readonly Dictionary<string, double> Pricing = new()
    {
        ["claude_opus"] = 15.00 / 1_000_000,
        ["gpt5_latest"] = 10.00 / 1_000_000,
    };

    private readonly string _basePath;

    public TokenTracker(string? basePath = null)
    {
        _basePath = basePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".code-index");

        Directory.CreateDirectory(_basePath);
    }

    private string SavingsPath => Path.Combine(_basePath, SavingsFileName);

    /// <summary>Add tokens saved to the running total. Returns new cumulative total.</summary>
    public int RecordSaving(int tokensSaved)
    {
        var data = LoadData();
        var delta = Math.Max(0, tokensSaved);
        var total = data.GetValueOrDefault("total_tokens_saved", 0) + delta;
        data["total_tokens_saved"] = total;

        try
        {
            var json = JsonSerializer.Serialize(data);
            File.WriteAllText(SavingsPath, json);
        }
        catch
        {
            // Silently ignore write failures
        }

        return total;
    }

    /// <summary>Return the current cumulative total without modifying it.</summary>
    public int GetTotalSavings()
    {
        var data = LoadData();
        return data.GetValueOrDefault("total_tokens_saved", 0);
    }

    /// <summary>Estimate tokens saved: (rawBytes - responseBytes) / bytesPerToken.</summary>
    public static int EstimateSavings(int rawBytes, int responseBytes)
    {
        return Math.Max(0, (rawBytes - responseBytes) / BytesPerToken);
    }

    /// <summary>
    /// Return cost avoided estimates for this call and the running total.
    /// </summary>
    public static Dictionary<string, Dictionary<string, double>> CostAvoided(
        int tokensSaved,
        int totalTokensSaved)
    {
        return new Dictionary<string, Dictionary<string, double>>
        {
            ["cost_avoided"] = Pricing.ToDictionary(
                kv => kv.Key,
                kv => Math.Round(tokensSaved * kv.Value, 4)),
            ["total_cost_avoided"] = Pricing.ToDictionary(
                kv => kv.Key,
                kv => Math.Round(totalTokensSaved * kv.Value, 4)),
        };
    }

    private Dictionary<string, int> LoadData()
    {
        try
        {
            if (File.Exists(SavingsPath))
            {
                var json = File.ReadAllText(SavingsPath);
                return JsonSerializer.Deserialize<Dictionary<string, int>>(json)
                       ?? new Dictionary<string, int>();
            }
        }
        catch
        {
            // Ignore read failures
        }

        return new Dictionary<string, int>();
    }
}
