namespace CTraderFeed.Tests;

/// <summary>
/// Proves the V7 execution path (TradePlanExecutionEngine.cs, TradePlanV7.cs)
/// never references the legacy dual-planning symbols named in
/// docs/adr-trade-plan-v7-boundary.md - a source-text check rather than a
/// runtime mock, so a future edit that adds a call site fails this test
/// immediately regardless of which code path exercises it.
/// </summary>
public sealed class TradePlanExecutionEngineDependencyTests
{
  private static readonly string[] ForbiddenSymbols =
  [
    "StructureStopPlanner",
    "StopPipsBounds",
    "BuildOpposingZoneContext",
    "StructuralStopIdentityMatches",
    "PlansMatchWithinTolerance",
    "ResolveExecutionRoute",
    "RecomputeStructureStopPlan",
  ];

  private static readonly string[] V7SourceFiles =
  [
    "TradePlanExecutionEngine.cs",
    "TradePlanV7.cs",
  ];

  private static string SourceDirectory()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(directory.FullName, "ctrader-engine", "src");
      if (Directory.Exists(candidate))
      {
        return candidate;
      }
      directory = directory.Parent;
    }
    throw new DirectoryNotFoundException(
      "ctrader-engine/src was not found above " + AppContext.BaseDirectory
    );
  }

  public static TheoryData<string, string> FileAndForbiddenSymbolPairs
  {
    get
    {
      var data = new TheoryData<string, string>();
      foreach (var file in V7SourceFiles)
      {
        foreach (var symbol in ForbiddenSymbols)
        {
          data.Add(file, symbol);
        }
      }
      return data;
    }
  }

  // Explanatory comments in these files legitimately *name* the forbidden
  // symbols (to say "this must never call X") - strip // and /// comment
  // text before scanning so the check is about actual code references,
  // not prose about the boundary itself.
  private static string StripLineComments(string text) =>
    string.Join(
      '\n',
      text.Split('\n').Select(line =>
      {
        var index = line.IndexOf("//", StringComparison.Ordinal);
        return index < 0 ? line : line[..index];
      })
    );

  [Theory]
  [MemberData(nameof(FileAndForbiddenSymbolPairs))]
  public void V7SourceFileNeverReferencesForbiddenSymbol(string file, string symbol)
  {
    var path = Path.Combine(SourceDirectory(), file);
    var code = StripLineComments(File.ReadAllText(path));

    Assert.DoesNotContain(symbol, code, StringComparison.Ordinal);
  }

  [Fact]
  public void EveryV7SourceFileExists()
  {
    foreach (var file in V7SourceFiles)
    {
      Assert.True(
        File.Exists(Path.Combine(SourceDirectory(), file)),
        $"expected {file} in ctrader-engine/src"
      );
    }
  }

  [Fact]
  public void GuardListCoversEveryDualPlanningSymbolNamedInTheAdr()
  {
    // A canary against silently trimming the forbidden list itself -
    // matches the exact symbol list docs/adr-trade-plan-v7-boundary.md and
    // the original architecture report identified as dual-planning.
    Assert.Equal(7, ForbiddenSymbols.Length);
    Assert.Contains("ResolveExecutionRoute", ForbiddenSymbols);
    Assert.Contains("StructureStopPlanner", ForbiddenSymbols);
  }
}
