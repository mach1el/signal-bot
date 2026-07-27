using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class TradePlanExecutionEngineTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  private static TradePlan MarketWatchPlan(
    string direction = "BUY",
    decimal zoneLow = 4088.10m,
    decimal zoneHigh = 4090.00m,
    decimal stopPrice = 4081.80m,
    long expiresAt = 1_720_003_600,
    int? maxSpreadTicks = 8,
    long maxVolume = 100_000
  ) => new(
    Version: 7,
    PlanId: "plan-1",
    ThesisId: "thesis-1",
    SetupId: "setup-1",
    Symbol: "XAU",
    CreatedAt: 1_720_000_000,
    ExpiresAt: expiresAt,
    Analysis: new TradePlanAnalysis(
      "Trend Pullback", "trend_pullback", direction,
      new[] { "M15" }, "M15", "M5", 1, 1, 0.8, 3, "up", "trend"
    ),
    SourceStructure: new TradePlanSourceStructure(
      "structure-1", "demand", "M15", zoneLow, zoneHigh, stopPrice
    ),
    Entry: new TradePlanEntry(
      "market_watch",
      expiresAt,
      ZoneLow: zoneLow,
      ZoneHigh: zoneHigh,
      Activation: "quote_inside_zone",
      PriceSide: direction == "BUY" ? "ask" : "bid",
      MaxSpreadTicks: maxSpreadTicks
    ),
    Stop: new TradePlanStop("absolute", stopPrice, "structural_invalidation"),
    Targets: new[]
    {
      new TradePlanTarget(
        "TP1", "absolute",
        direction == "BUY" ? zoneHigh + 6m : zoneLow - 6m,
        0.40m
      ),
      new TradePlanTarget(
        "TP2", "absolute",
        direction == "BUY" ? zoneHigh + 14m : zoneLow - 14m,
        0.35m
      ),
      new TradePlanTarget(
        "TP3", "absolute",
        direction == "BUY" ? zoneHigh + 25m : zoneLow - 25m,
        0.25m
      ),
    },
    Risk: new TradePlanRisk(1.0m, 1.0m, maxVolume, 2.0m),
    Management: new TradePlanManagement("TP1", 6, true),
    ExecutionPolicy: new TradePlanExecutionPolicy(true, false, true, true),
    Provenance: new TradePlanProvenance("v7", "map-1", "cfg-1")
  );

  [Fact]
  public void MarketWatchSubmitsWhenAskInsideZone()
  {
    var plan = MarketWatchPlan();

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.SubmitMarket, decision.Action);
    Assert.True(decision.ShouldSubmit);
  }

  [Fact]
  public void MarketWatchWaitsWhenAskOutsideZone()
  {
    var plan = MarketWatchPlan();

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4085.0m, ask: 4085.2m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.False(decision.ShouldSubmit);
    Assert.Null(decision.RejectReason);
  }

  [Fact]
  public void MarketWatchWaitsWhenSpreadExceedsDeclaredLimit()
  {
    var plan = MarketWatchPlan(maxSpreadTicks: 8);

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 20m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("spread_exceeds_declared_limit", decision.RejectReason);
  }

  [Fact]
  public void ExpiredPlanIsRejectedRegardlessOfQuote()
  {
    var plan = MarketWatchPlan(expiresAt: 1_720_000_050);

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4088.9m, ask: 4089.0m, spreadTicks: 2m, nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal("plan_expired", decision.RejectReason);
  }

  [Fact]
  public void CalculateVolumeSizesFromRiskPercentAndStopDistance()
  {
    var plan = MarketWatchPlan();
    // entry reference (BUY) = min(zoneLow, zoneHigh) = 4088.10, stop = 4081.80
    // stop distance = 6.30, pipSize = 0.1 -> 63 pips.
    // risk = 10000 * 1% = 100; riskLots = 100 / (63 * 10) = 0.1587...

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, accountBalance: 10_000m, pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.True(result.TotalVolume > 0);
    Assert.Equal(3, result.Slices.Count);
    Assert.Equal(result.TotalVolume, result.Slices.Sum(slice => slice.Volume));
  }

  [Fact]
  public void CalculateVolumeClampsToDeclaredMaxVolume()
  {
    var plan = MarketWatchPlan(maxVolume: 500);

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, accountBalance: 1_000_000m, pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.True(result.TotalVolume <= 500);
  }

  [Fact]
  public void CalculateVolumeSlicesAreProportionalToCloseRatio()
  {
    var plan = MarketWatchPlan();

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, accountBalance: 200_000m, pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    var tp1 = result.Slices.Single(slice => slice.TargetId == "TP1").Volume;
    var tp3 = result.Slices.Single(slice => slice.TargetId == "TP3").Volume;
    // TP1 close_ratio 0.40 > TP3 close_ratio 0.25.
    Assert.True(tp1 >= tp3);
  }

  [Fact]
  public void HasReachedTargetUsesDirectionCorrectSide()
  {
    var buyPlan = MarketWatchPlan(direction: "BUY");
    var sellPlan = MarketWatchPlan(direction: "SELL", zoneLow: 4110.00m, zoneHigh: 4112.00m, stopPrice: 4118.50m);
    var buyTarget = buyPlan.Targets[0];
    var sellTarget = sellPlan.Targets[0];

    Assert.True(TradePlanExecutionEngine.HasReachedTarget(buyPlan, buyTarget, buyTarget.Price));
    Assert.False(
      TradePlanExecutionEngine.HasReachedTarget(buyPlan, buyTarget, buyTarget.Price - 1m)
    );
    Assert.True(
      TradePlanExecutionEngine.HasReachedTarget(sellPlan, sellTarget, sellTarget.Price)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedTarget(sellPlan, sellTarget, sellTarget.Price + 1m)
    );
  }

  [Fact]
  public void BreakEvenMatchesWorkedXauExampleForBuy()
  {
    var plan = MarketWatchPlan(direction: "BUY");

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4081.80m, symbol: Symbol
    );

    Assert.Equal(4087.72m, result.DesiredStop);
    Assert.Equal(4087.72m, result.NewStop);
    Assert.True(result.Improved);
  }

  [Fact]
  public void BreakEvenMatchesWorkedXauExampleForSell()
  {
    var plan = MarketWatchPlan(direction: "SELL", zoneLow: 4110.00m, zoneHigh: 4112.00m, stopPrice: 4118.50m);

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4118.50m, symbol: Symbol
    );

    Assert.Equal(4087.60m, result.DesiredStop);
    Assert.Equal(4087.60m, result.NewStop);
  }

  [Fact]
  public void BreakEvenNeverWorsensAnAlreadyBetterStop()
  {
    var plan = MarketWatchPlan(direction: "BUY");

    var result = TradePlanExecutionEngine.CalculateBreakEven(
      plan, brokerConfirmedFillPrice: 4087.66m, currentStop: 4090.00m, symbol: Symbol
    );

    Assert.Equal(4090.00m, result.NewStop);
    Assert.False(result.Improved);
  }
}
