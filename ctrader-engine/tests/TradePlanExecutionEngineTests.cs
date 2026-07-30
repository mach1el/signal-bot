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
    // market_watch submits the whole position as one order - no per-target
    // slice exists to compute. TP close volume is worked out live from
    // RemainingVolume at each target hit (TradePlanRuntime), never from a
    // pre-built list here.
    Assert.Empty(result.Slices);
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

  private static TradePlan LimitLadderPlan(
    string direction = "BUY",
    decimal legPrice1 = 4089.50m,
    decimal legPrice2 = 4085.00m,
    decimal leg1Ratio = 0.60m,
    decimal leg2Ratio = 0.40m,
    decimal stopPrice = 4079.00m,
    long maxVolume = 100_000
  ) => new(
    Version: 7,
    PlanId: "plan-1",
    ThesisId: "thesis-1",
    SetupId: "setup-1",
    Symbol: "XAU",
    CreatedAt: 1_720_000_000,
    ExpiresAt: 1_720_003_600,
    Analysis: new TradePlanAnalysis(
      "Structural Zone Reaction", "structural_zone", direction,
      new[] { "M15" }, "M15", "M5", 1, 1, 0.65, 2, "up", "range"
    ),
    SourceStructure: new TradePlanSourceStructure(
      "structure-1", "demand", "M15", legPrice2, legPrice1, stopPrice
    ),
    Entry: new TradePlanEntry(
      "limit_ladder",
      1_720_003_600,
      ZoneLow: legPrice2,
      ZoneHigh: legPrice1,
      Legs: new[]
      {
        new TradePlanEntryLeg("L1", legPrice1, leg1Ratio),
        new TradePlanEntryLeg("L2", legPrice2, leg2Ratio),
      }
    ),
    Stop: new TradePlanStop("absolute", stopPrice, "structural_invalidation"),
    Targets: new[] { new TradePlanTarget("TP1", "absolute", legPrice1 + 8m, 1.0m) },
    Risk: new TradePlanRisk(1.0m, 1.0m, maxVolume, 2.0m),
    Management: new TradePlanManagement(null, 6, true),
    ExecutionPolicy: new TradePlanExecutionPolicy(false, true, true, true),
    Provenance: new TradePlanProvenance("v7", "map-1", "cfg-1")
  );

  [Fact]
  public void CalculateVolumeSlicesForALimitLadderAreProportionalToLegVolumeRatio()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.60m, leg2Ratio: 0.40m);

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, accountBalance: 200_000m, pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(2, result.Slices.Count);
    Assert.Equal(result.TotalVolume, result.Slices.Sum(slice => slice.Volume));
    var l1 = result.Slices.Single(slice => slice.TargetId == "L1").Volume;
    var l2 = result.Slices.Single(slice => slice.TargetId == "L2").Volume;
    // L1 volume_ratio 0.60 > L2 volume_ratio 0.40 - and this must track the
    // leg ratio, not plan.Targets.CloseRatio (this plan's single TP1 has
    // close_ratio 1.0, which would say nothing about a 60/40 split).
    Assert.True(l1 > l2);
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
