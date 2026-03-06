using JCodeMunch.Mcp.Parser;
using JCodeMunch.Mcp.Security;
using JCodeMunch.Mcp.Storage;
using JCodeMunch.Mcp.Summarizer;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ModelContextProtocol;

// Apply extra extension mappings from environment
LanguageRegistry.ApplyExtraExtensions();

var builder = Host.CreateApplicationBuilder(args);

// Configure logging
builder.Logging.ClearProviders();

var logLevel = Environment.GetEnvironmentVariable("JCODEMUNCH_LOG_LEVEL") switch
{
    "DEBUG" => LogLevel.Debug,
    "INFO" => LogLevel.Information,
    "WARNING" => LogLevel.Warning,
    "ERROR" => LogLevel.Error,
    _ => LogLevel.Warning,
};

var logFile = Environment.GetEnvironmentVariable("JCODEMUNCH_LOG_FILE");
if (!string.IsNullOrEmpty(logFile))
{
    // File logging would need a provider like Serilog; for now log to console
    builder.Logging.AddConsole();
}
else
{
    builder.Logging.AddConsole();
}

builder.Logging.SetMinimumLevel(logLevel);

// Register services via DI
var storagePath = Environment.GetEnvironmentVariable("CODE_INDEX_PATH");

builder.Services.AddSingleton(_ => new IndexStore(storagePath));
builder.Services.AddSingleton(_ => new TokenTracker(storagePath));
builder.Services.AddSingleton<SymbolExtractor>();
builder.Services.AddSingleton<BatchSummarizer>();

// Configure MCP server with stdio transport and assembly tool scanning
builder.Services
    .AddMcpServer()
    .WithStdioServerTransport()
    .WithToolsFromAssembly();

var app = builder.Build();
await app.RunAsync();
