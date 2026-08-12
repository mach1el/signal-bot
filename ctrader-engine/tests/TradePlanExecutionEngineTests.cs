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
    Provenance: new TradePlanProvenance("v7", "map-1", "cfg-1"),
    Sizing: new TradePlanSizing(
      "equity_table", "owner_equity_v1", "single", Array.Empty<decimal>()
    )
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
    // Live incident: a market_watch plan expired unfilled even though price
    // logs showed it re-entering the zone later - Wait decisions used to
    // carry no reason at all, so a poll's outcome was completely invisible
    // in production logs, making that "did it ever actually see the zone
    // again" question unanswerable after the fact. Naming this reason
    // (distinct from spread_exceeds_declared_limit) lets a future expiry
    // log line say which one actually happened.
    Assert.Equal("outside_zone", decision.RejectReason);
  }

  [Fact]
  public void MarketWatchSubmitsWhenBidInsideZoneEvenIfAskPokesJustAbove()
  {
    // 04 Aug 23:42 incident: BUY zone [4085.71, 4086.11] (0.40 wide, ~4
    // pips). Bid-based M1 bars showed price genuinely trading at 4086.03,
    // inside the zone, but the plan still expired 7 minutes later with
    // last_wait_reason=outside_zone. Root cause: the ask-only check
    // required the single trade-side quote strictly inside a zone
    // narrower than typical spread, so a normal spread of a few ticks put
    // ask just above zone high the entire time bid sat inside it. Reproduce
    // that exact geometry: bid inside, ask a few ticks over the top edge.
    var plan = MarketWatchPlan(
      zoneLow: 4085.71m, zoneHigh: 4086.11m, maxSpreadTicks: null
    );

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4086.03m, ask: 4086.15m, spreadTicks: 12m,
      nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.SubmitMarket, decision.Action);
    Assert.True(decision.ShouldSubmit);
  }

  [Fact]
  public void MarketWatchWaitsWhenNeitherBidNorAskReachTheZone()
  {
    // A spread wide enough that the whole [bid, ask] range still sits
    // clear of the zone must still wait - this is not "any touch fires",
    // it is "the tradable range overlaps the zone".
    var plan = MarketWatchPlan(
      zoneLow: 4085.71m, zoneHigh: 4086.11m, maxSpreadTicks: null
    );

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4086.20m, ask: 4086.30m, spreadTicks: 10m,
      nowUnixSeconds: 1_720_000_100
    );

    Assert.Equal(TradePlanEntryAction.Wait, decision.Action);
    Assert.Equal("outside_zone", decision.RejectReason);
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

  private static TradingAccountSnapshot Account(
    decimal equity,
    decimal? balance = null
  ) => new(
    1,
    IsLive: false,
    PermissionScope: "ScopeTrade",
    AccessRights: "FullAccess",
    AccountType: "Hedged",
    BrokerName: "Fusion Markets",
    Balance: balance ?? equity,
    Equity: equity,
    SnapshotTimestamp: 0
  );

  [Fact]
  public void CalculateVolumeWithoutSizingContractIsRejected()
  {
    var plan = MarketWatchPlan() with { Sizing = null };

    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        plan, Account(10_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
      )
    );

    Assert.Equal("legacy_v7_sizing_contract_missing", error.Message);
  }

  [Fact]
  public void CalculateVolumeUsesEquityTableIgnoringRiskPercent()
  {
    // RiskPercent=1 on a ~63-pip stop at equity 1300 would yield ~0.02 lots
    // under the old risk path; equity_table must produce table lots instead.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume); // LotsForEquity(1300)=0.12
    Assert.Empty(result.Slices);
    Assert.NotEqual(300, result.TotalVolume);
  }

  [Fact]
  public void CalculateVolumeScalesEquityTableByRiskMultiplier()
  {
    // Owner: scalp stamps risk_multiplier=2 → 2× equity-table lots when
    // equity is at/above $2k. Stop geometry is unchanged; volume only.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 2.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(2_500m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(2500)=0.15 → ×2 = 0.30 lots → 3000 volume units.
    Assert.Equal(3_000, result.TotalVolume);
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeUsesOnePointFiveScalpBoostBelowTwoThousandEquity()
  {
    // Owner 2026-08-12: below $2k, scalp ×2 stamp becomes ×1.5 table lots
    // (0.10 → 0.15), not double and not half.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 2.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(800m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(800)=0.10 → ×1.5 = 0.15 lots → 1500 volume units.
    Assert.Equal(1_500, result.TotalVolume);
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeKeepsUnitMultiplierBelowTwoThousandEquity()
  {
    // Reaction / non-scalp stamps 1.0 — do not force the low-equity scalp cut.
    var plan = MarketWatchPlan() with
    {
      Risk = new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
    };

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(800m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    // LotsForEquity(800)=0.10 → ×1 = 0.10 lots → 1000 volume units.
    Assert.Equal(1_000, result.TotalVolume);
  }

  [Fact]
  public void CalculateVolumeSizesFromEquityTable()
  {
    var plan = MarketWatchPlan();

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(10_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(3_000, result.TotalVolume); // LotsForEquity(10000)=0.30
    Assert.Empty(result.Slices);
  }

  [Fact]
  public void CalculateVolumeRejectsWhenTableExceedsDeclaredMaxVolume()
  {
    var plan = MarketWatchPlan(maxVolume: 500);

    var error = Assert.Throws<TradePlanContractException>(() =>
      TradePlanExecutionEngine.CalculateVolume(
        plan, Account(1_000_000m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
      )
    );

    Assert.Equal("equity_table_above_broker_maximum", error.Message);
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
      "Zone Reaction", "supply_demand", direction,
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
    Provenance: new TradePlanProvenance("v7", "map-1", "cfg-1"),
    Sizing: new TradePlanSizing(
      "equity_table",
      "owner_equity_v1",
      "zone_scale",
      new[] { leg1Ratio, leg2Ratio }
    )
  );

  [Fact]
  public void CalculateVolumeSlicesForMarketWithLimitScaleMatchEquity1300()
  {
    var plan = new TradePlan(
      Version: 7,
      PlanId: "plan-mwls",
      ThesisId: "thesis-1",
      SetupId: "setup-1",
      Symbol: "XAU",
      CreatedAt: 1_720_000_000,
      ExpiresAt: 1_720_003_600,
      Analysis: new TradePlanAnalysis(
        "Key Level Reaction", "key_level", "BUY",
        new[] { "M15" }, "M15", "M5", 1, 1, 0.65, 2, "up", "range"
      ),
      SourceStructure: new TradePlanSourceStructure(
        "structure-1", "key_level", "M15", 4085.00m, 4089.50m, 4079.00m
      ),
      Entry: new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        Legs: new[]
        {
          new TradePlanEntryLeg(
            "L1", 4089.10m, 0.70m, TradePlanContract.OrderTypeMarket
          ),
          new TradePlanEntryLeg(
            "L2", 4085.00m, 0.30m, TradePlanContract.OrderTypeLimit
          ),
        }
      ),
      Stop: new TradePlanStop("absolute", 4079.00m, "structural_invalidation"),
      Targets: new[] { new TradePlanTarget("TP1", "absolute", 4097.00m, 1.0m) },
      Risk: new TradePlanRisk(1.0m, 1.0m, 100_000, 2.0m),
      Management: new TradePlanManagement(null, 6, true),
      ExecutionPolicy: new TradePlanExecutionPolicy(true, true, true, true),
      Provenance: new TradePlanProvenance("v7", "map-1", "cfg-1"),
      Sizing: new TradePlanSizing(
        "equity_table",
        "owner_equity_v1",
        "zone_scale",
        new[] { 0.70m, 0.30m }
      )
    );

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume);
    Assert.Equal(800, result.Slices.Single(s => s.TargetId == "L1").Volume);
    Assert.Equal(400, result.Slices.Single(s => s.TargetId == "L2").Volume);
  }

  [Fact]
  public void EvaluateEntrySubmitsMarketWithLimitScaleImmediately()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m) with
    {
      Entry = new TradePlanEntry(
        TradePlanContract.EntryTypeMarketWithLimitScale,
        1_720_003_600,
        ZoneLow: 4085.00m,
        ZoneHigh: 4089.50m,
        Legs: new[]
        {
          new TradePlanEntryLeg("L1", 4089.10m, 0.70m, "market"),
          new TradePlanEntryLeg("L2", 4085.00m, 0.30m, "limit"),
        }
      ),
    };

    var decision = TradePlanExecutionEngine.EvaluateEntry(
      plan, bid: 4089.0m, ask: 4089.1m, spreadTicks: 1m, nowUnixSeconds: 1_720_000_100
    );

    Assert.True(decision.ShouldSubmit);
    Assert.Equal(TradePlanEntryAction.SubmitLadder, decision.Action);
  }

  [Fact]
  public void CalculateVolumeSlicesForALimitLadderAreProportionalToLegVolumeRatio()
  {
    var plan = LimitLadderPlan(leg1Ratio: 0.70m, leg2Ratio: 0.30m);

    var result = TradePlanExecutionEngine.CalculateVolume(
      plan, Account(1_300m), pipSize: 0.1m, pipValuePerLot: 10m, symbol: Symbol
    );

    Assert.Equal(1_200, result.TotalVolume);
    Assert.Equal(2, result.Slices.Count);
    Assert.Equal(result.TotalVolume, result.Slices.Sum(slice => slice.Volume));
    var l1 = result.Slices.Single(slice => slice.TargetId == "L1").Volume;
    var l2 = result.Slices.Single(slice => slice.TargetId == "L2").Volume;
    // SplitEntryVolume step-aligns 70/30 on 0.12 lots → 0.08 + 0.04.
    Assert.Equal(800, l1);
    Assert.Equal(400, l2);
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
  public void SellWholeNumberTargetGetsVipHandleCushion()
  {
    // Mirror algo-bot watcher._tp_hit: whole handle 4408 books when ask is
    // still a few ticks above (spread), but not a full point above.
    Assert.True(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4408.86m, 4408.00m)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4409.00m, 4408.00m)
    );
  }

  [Fact]
  public void SellDecimalTargetStaysExact()
  {
    Assert.True(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4408.50m, 4408.50m)
    );
    Assert.False(
      TradePlanExecutionEngine.HasReachedExitTarget("SELL", 4408.51m, 4408.50m)
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
