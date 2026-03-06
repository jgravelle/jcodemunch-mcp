namespace JCodeMunch.Mcp.Models;

/// <summary>
/// A node in the symbol tree with children.
/// Used to build hierarchical outlines (methods nested under classes).
/// </summary>
public sealed class SymbolNode
{
    public required Symbol Symbol { get; init; }
    public List<SymbolNode> Children { get; } = [];

    /// <summary>
    /// Build a hierarchical tree from a flat symbol list.
    /// Methods become children of their parent classes.
    /// Returns top-level symbols (classes and standalone functions).
    /// </summary>
    public static List<SymbolNode> BuildTree(IReadOnlyList<Symbol> symbols)
    {
        var nodeMap = new Dictionary<string, SymbolNode>(symbols.Count);
        foreach (var s in symbols)
        {
            nodeMap[s.Id] = new SymbolNode { Symbol = s };
        }

        var roots = new List<SymbolNode>();
        foreach (var symbol in symbols)
        {
            var node = nodeMap[symbol.Id];
            if (symbol.Parent is not null && nodeMap.TryGetValue(symbol.Parent, out var parentNode))
            {
                parentNode.Children.Add(node);
            }
            else
            {
                roots.Add(node);
            }
        }

        return roots;
    }

    /// <summary>
    /// Flatten symbol tree with depth information for indentation.
    /// </summary>
    public static List<(Symbol Symbol, int Depth)> FlattenTree(IReadOnlyList<SymbolNode> nodes, int depth = 0)
    {
        var result = new List<(Symbol, int)>();
        foreach (var node in nodes)
        {
            result.Add((node.Symbol, depth));
            result.AddRange(FlattenTree(node.Children, depth + 1));
        }

        return result;
    }
}
