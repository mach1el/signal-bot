using System.Globalization;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class StopTrailPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2
  );

  private static readonly JsonDocument Fixture = LoadFixture();

  private static JsonDocument LoadFixture()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(
        directory.FullName,
        "contracts",
        "autotrade",
        "stop-trail-parity.json"
      );
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate));
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException(
      "contracts/autotrade/stop-trail-parity.json was not found above "
      + AppContext.BaseDirectory
    );
  }

  private static IEnumerable<JsonElement> ParityCases() =>
    Fixture.RootElement.GetProperty("cases").EnumerateArray();

  private static decimal Number(JsonElement element, string name) =>
    decimal.Parse(
      element.GetProperty(name).GetString()!,
      CultureInfo.InvariantCulture
    );

  public static TheoryData<string> ParityCaseNames
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var item in ParityCases())
      {
        data.Add(item.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  [Theory]
  [MemberData(nameof(ParityCaseNames))]
  public void SharedStopTrailFixtureMatchesPlanner(string name)
  {
    var root = Fixture.RootElement;
    var item = ParityCases().Single(entry =>
      entry.GetProperty("name").GetString() == name
    );
    var pipSize = Number(root, "pip_size");
    var bufferTicks = root.GetProperty("break_even_buffer_ticks").GetInt32();
    var direction = item.GetProperty("direction").GetString() == "BUY"
      ? TradeDirection.Buy
      : TradeDirection.Sell;
    var entry = Number(item, "entry_price");
    var completedTargetIndex = item.GetProperty("completed_target_index").GetInt32();
    var initialStop = direction == TradeDirection.Buy
      ? entry - 6.5m
      : entry + 6.5m;
    var state = State(direction, entry, initialStop);

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(
        state,
        completedTargetIndex,
        Symbol,
        pipSize,
        bufferTicks
      )
    );

    Assert.Equal(Number(item, "expected_stop"), move.StopLoss);
    Assert.Equal(item.GetProperty("expected_label").GetString(), move.Label);
    Assert.Equal(Number(item, "expected_offset"), move.BufferPrice);
    Assert.Equal(
      Number(item, "expected_offset"),
      Math.Abs(move.StopLoss - entry)
    );
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4000.26, 4003.2, 4006.2)]
  [InlineData(TradeDirection.Sell, 4000.14, 3997.2, 3994.2)]
  public void HoldsAfterTp2ThenTrailsTwoTargetsBehind(
    TradeDirection direction,
    double afterTp1,
    double afterTp3,
    double afterTp4
  )
  {
    var state = State(direction);
    var tp1 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp1), tp1.StopLoss);
    Assert.Equal("BE+6 ticks", tp1.Label);
    Assert.Equal(0.06m, Math.Abs(tp1.StopLoss - state.EntryPrice));
    state = state with { CurrentStopLoss = tp1.StopLoss };

    Assert.Null(StopTrailPlanner.Plan(state, 1, Symbol, 0.1m, 6));

    var tp3 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 2, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp3), tp3.StopLoss);
    Assert.Equal("TP1", tp3.Label);
    state = state with { CurrentStopLoss = tp3.StopLoss };

    var tp4 = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 3, Symbol, 0.1m, 6)
    );
    Assert.Equal(Convert.ToDecimal(afterTp4), tp4.StopLoss);
    Assert.Equal("TP2", tp4.Label);
    Assert.Null(StopTrailPlanner.Plan(state, 4, Symbol, 0.1m, 6));
  }

  [Fact]
  public void UsesOriginalOrdinalsForAdaptiveTargetPlans()
  {
    var state = State(TradeDirection.Buy) with
    {
      Slices = [200, 200, 200, 200],
      TargetsPips = [30, 90, 120, 200],
      TargetOrdinals = [1, 3, 4, 5],
    };

    var move = Assert.IsType<StopTrailMove>(
      StopTrailPlanner.Plan(state, 1, Symbol, 0.1m, 6)
    );

    Assert.Equal(4003.2m, move.StopLoss);
    Assert.Equal("TP1", move.Label);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4004.0)]
  [InlineData(TradeDirection.Sell, 3996.0)]
  public void IgnoresStopThatWouldMoveBackward(
    TradeDirection direction,
    double currentStop
  )
  {
    var state = State(direction) with
    {
      CurrentStopLoss = Convert.ToDecimal(currentStop),
    };

    Assert.Null(StopTrailPlanner.Plan(state, 0, Symbol, 0.1m, 6));
  }

  private static AutoTradePositionState State(
    TradeDirection direction,
    decimal entryPrice = 4000.2m,
    decimal? currentStopLoss = null
  ) => new(
    "candidate",
    91,
    7,
    direction,
    entryPrice,
    1_000,
    1_000,
    [200, 200, 200, 200, 200],
    [30, 60, 90, 120, 200],
    0,
    1_000,
    currentStopLoss
      ?? (direction == TradeDirection.Buy ? entryPrice - 6.5m : entryPrice + 6.5m)
  );
}
