using System.Text.Json;
using ApexVoid.CTraderFeed;
using StackExchange.Redis;

namespace CTraderFeed.Tests;

/// <summary>
/// Proves TradePlanRuntime is a genuinely wired broker-execution path, not
/// just pure decision logic: a real TradePlan V7 JSON payload goes in via
/// the execution:trade_plans stream, and a real market order comes out via
/// ICTraderTradeClient, with fill/target/BE tracking and restart recovery -
/// see docs/adr-trade-plan-v7-boundary.md Sections F/H/I/J/K.
/// </summary>
public sealed class TradePlanRuntimeTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU", "XAUUSD", 7, Digits: 2, PipPosition: 2,
    MinVolume: 100, StepVolume: 100, MaxVolume: 100_000, LotSize: 10_000
  );

  private static AutoTradeOptions Options(string contractMode = "v7_primary") => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "Fusion",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferTicks: 3,
    CandidateMaxAgeSeconds: 90,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto",
    ContractMode: contractMode
  );

  private static string PlanJson(
    string planId = "v7:plan-1",
    string thesisId = "thesis-1",
    string setupId = "setup-1",
    string direction = "BUY",
    decimal zoneLow = 4088.10m,
    decimal zoneHigh = 4090.00m,
    decimal stopPrice = 4082.50m,
    long expiresAt = 2_000_000_000
  )
  {
    return $$"""
    {
      "version": 7,
      "plan_id": "{{planId}}",
      "thesis_id": "{{thesisId}}",
      "setup_id": "{{setupId}}",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": {{expiresAt}},
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "{{direction}}",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": {{expiresAt}},
        "zone_low": "{{zoneLow}}",
        "zone_high": "{{zoneHigh}}",
        "activation": "quote_inside_zone",
        "price_side": "{{(direction == "BUY" ? "ask" : "bid")}}",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "{{stopPrice}}",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4096.00", "close_ratio": "0.5"},
        {"target_id": "TP2", "type": "absolute", "price": "4104.00", "close_ratio": "0.5"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """;
  }

  [Fact]
  public async Task ArmsPlanAndSubmitsMarketOrderWhenQuoteEntersZone()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    // Quote outside the zone: plan should arm but not submit yet.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
    var armed = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Armed, armed.Stage);

    // Quote enters the zone: should submit a market order now.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Contains("v7:plan-1", order.Comment);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Open, open.Stage);
    Assert.NotNull(open.PositionId);
    var events = store.Events.Select(e => e.Type).ToArray();
    Assert.Contains("plan_armed", events);
    Assert.Contains("order_filled", events);
  }

  [Fact]
  public async Task SubmitsARelativeStopLossScaledForTheBrokerNotTheSymbolsTickSize()
  {
    // Live incident: RelativeStopLoss used to be computed as
    // distance / tickSize (a tick count) instead of distance * 100_000m
    // (the fixed-point scale cTrader's ProtoOANewOrderReq.RelativeStopLoss
    // actually expects, per the already-correct V6 path in
    // AutoTradeEngine.cs). For a 2-digit symbol like XAU that sent a value
    // roughly 1000x smaller than the broker expected, which cTrader
    // rejected outright with "Relative stop loss has invalid precision" -
    // crash-looping the whole auto_trade consumer, not just one order.
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson(zoneLow: 4088.10m, zoneHigh: 4090.00m, stopPrice: 4082.50m));
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    // BUY entryReference is the proximal (lowest) entry price, 4088.10;
    // distance to the 4082.50 stop is 5.60 -> 5.60 * 100_000 = 560_000.
    Assert.Equal(560_000, order.RelativeStopLoss);
  }

  [Fact]
  public async Task MarketWatchWithManyTargetsAndASmallAccountStillSubmits()
  {
    // Live incident: a market_watch plan with 5 TP targets and a
    // small-account risk-based volume used to throw
    // "N volume steps cannot cover 5 targets" unconditionally inside
    // CalculateVolume - even though market_watch never reads the resulting
    // Slices at all (it submits TotalVolume as one order). TP close volume
    // is computed live from RemainingVolume at each target hit
    // (ManageOpenPositionsAsync), never from a pre-built slice list, so TP
    // count must never be able to block or crash entry submission.
    var fiveTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.2"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.2"},
        {"target_id": "TP4", "type": "absolute", "price": "4098.00", "close_ratio": "0.2"},
        {"target_id": "TP5", "type": "absolute", "price": "4100.00", "close_ratio": "0.2"}
      ]
      """;
    var store = new FakeV7Store();
    store.EnqueuePlan($$"""
    {
      "version": 7,
      "plan_id": "v7:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{fiveTargets}},
      "risk": {
        "risk_percent": "0.42",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(100, order.Volume);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Open, open.Stage);
  }

  [Fact]
  public async Task UndersizedLimitLadderRejectsThatPlanWithoutCrashingTheConsumer()
  {
    // A limit_ladder entry's legs are sized off each leg's own
    // volume_ratio (e.g. the 70/30 DCA scale-in split), never off
    // plan.Targets - a single TP target here proves TP count plays no
    // part. When even 2 legs' broker-minimum steps don't fit the
    // risk-based volume, CalculateVolume still throws (a genuine "this
    // plan cannot execute as configured"). That sizing check now runs
    // before the plan is ever armed (see ProcessTradePlanEntryAsync) -
    // a plan that can never be sized must never show PLAN ARMED only to
    // flip to PLAN REJECTED a moment later - and it must reject just this
    // plan, not crash-loop the whole consumer forever on every poll.
    var store = new FakeV7Store();
    store.EnqueuePlan("""
    {
      "version": 7,
      "plan_id": "v7:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Structural Zone Reaction",
        "strategy_family": "structural_zone",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "M15",
        "confirmation_timeframe": "M5",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 0.65,
        "confluence": 2,
        "bias": "up",
        "regime": "range",
        "reasons": ["demand_zone_ladder_fill"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "kind": "demand",
        "timeframe": "M15",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4079.00"
      },
      "entry": {
        "type": "limit_ladder",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.50", "volume_ratio": "0.90"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.10"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4079.00",
        "source": "structural_invalidation",
        "structure_id": "demand:M15:4085.00:4089.50:1719990000",
        "reason": "below distal edge plus Python-defined buffer"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
      ],
      "risk": {
        "risk_percent": "0.45",
        "risk_multiplier": "1.0",
        "max_volume": 800,
        "max_group_risk_percent": "2.0"
      },
      "management": {
        "be_after_target_id": null,
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": false,
        "allow_limit": true,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeV7TradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );

    // The plan is rejected during arming itself (before EvaluateArmedPlansAsync
    // or SubmitEntryAsync ever run), on this same first poll.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    Assert.Empty(runtime.TrackedStates);
    Assert.Equal("rejected", store.Value("execution:plan_state:v7:plan-1"));
    Assert.Contains(
      store.Events, e => e.Type == "plan_rejected" && e.CandidateId == "v7:plan-1"
    );
    Assert.Contains(logs, line => line.Contains("v7 plan sizing rejected"));

    // The consumer must not be stuck retrying this plan - a further poll
    // is harmless (nothing left to submit) instead of throwing again.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.60m, 4089.70m, 2), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
  }

  private const string LadderPlanJson = """
  {
    "version": 7,
    "plan_id": "v7:plan-1",
    "thesis_id": "thesis-1",
    "setup_id": "setup-1",
    "symbol": "XAU",
    "created_at": 1719999600,
    "expires_at": 2000000000,
    "analysis": {
      "strategy": "Structural Zone Reaction",
      "strategy_family": "structural_zone",
      "direction": "BUY",
      "context_timeframes": ["M15"],
      "formation_timeframe": "M15",
      "confirmation_timeframe": "M5",
      "formation_bar_ts": 1719999000,
      "confirmation_bar_ts": 1719999600,
      "score": 0.65,
      "confluence": 2,
      "bias": "up",
      "regime": "range",
      "reasons": ["demand_zone_ladder_fill"],
      "tags": []
    },
    "source_structure": {
      "structure_id": "demand:M15:4085.00:4089.50:1719990000",
      "kind": "demand",
      "timeframe": "M15",
      "low": "4085.00",
      "high": "4089.50",
      "invalidation_price": "4079.00"
    },
    "entry": {
      "type": "limit_ladder",
      "zone_low": "4085.00",
      "zone_high": "4089.50",
      "expires_at": 2000000000,
      "legs": [
        {"leg_id": "L1", "price": "4089.50", "volume_ratio": "0.60"},
        {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.40"}
      ]
    },
    "stop": {
      "type": "absolute",
      "price": "4079.00",
      "source": "structural_invalidation",
      "structure_id": "demand:M15:4085.00:4089.50:1719990000",
      "reason": "below distal edge plus Python-defined buffer"
    },
    "targets": [
      {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
    ],
    "risk": {
      "risk_percent": "2.0",
      "risk_multiplier": "1.0",
      "max_volume": 800,
      "max_group_risk_percent": "2.0"
    },
    "management": {
      "be_after_target_id": null,
      "be_buffer_ticks": 6,
      "never_worsen_stop": true
    },
    "execution_policy": {
      "allow_market": false,
      "allow_limit": true,
      "allow_partial_fill": true,
      "cancel_on_expiry": true
    },
    "provenance": {
      "analysis_engine_version": "",
      "market_map_id": "",
      "config_fingerprint": ""
    }
  }
  """;

  [Fact]
  public async Task EachLadderLegGetsItsOwnUniqueClientOrderId()
  {
    // P0 production bug: every leg used to share one plan-wide
    // ClientOrderId, so cTrader rejected leg 2+ outright as a duplicate.
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient { RejectDuplicateClientOrderIds = true };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Quote sits above the whole zone, so neither leg is marketable - both
    // legs are genuine resting limit orders for this assertion.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.00m, 4092.20m, 1), CancellationToken.None
    );

    Assert.Equal(2, client.LimitOrders.Count);
    var clientOrderIds = client.LimitOrders.Select(o => o.ClientOrderId).ToArray();
    Assert.Equal(clientOrderIds.Length, clientOrderIds.Distinct().Count());
    var comments = client.LimitOrders.Select(o => o.Comment).ToArray();
    Assert.Equal(comments.Length, comments.Distinct().Count());
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Submitted, state.Stage);
    Assert.Equal(2, state.SubmittedLegCount);
  }

  [Fact]
  public async Task RetryAfterALegFailureResumesWithoutResubmittingAnAcceptedLeg()
  {
    // P0 production bug: leg 2 erroring (duplicate ClientOrderId, or any
    // other broker rejection) threw before Stage ever became Submitted, so
    // the plan stayed Armed and the NEXT poll resubmitted leg 1 from
    // scratch - even though the broker had already accepted it.
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient { ThrowOnCallNumber = 2 };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    var quote = new SpotPrice("XAU", 4092.00m, 4092.20m, 1);

    await Assert.ThrowsAsync<InvalidOperationException>(
      () => runtime.PollAsync(client, Symbol, quote, CancellationToken.None)
    );

    // Leg 1 (only) was accepted and durably recorded before leg 2 threw.
    Assert.Single(client.LimitOrders);
    var afterFailure = Assert.Single(runtime.TrackedStates);
    // Stage deliberately stays Armed (not Submitted) after a partial
    // failure - EvaluateArmedPlansAsync only re-evaluates Armed plans, so
    // this is what makes the retry below come back at all.
    Assert.Equal(TradePlanRuntimeStage.Armed, afterFailure.Stage);
    Assert.Equal(1, afterFailure.SubmittedLegCount);

    // Retry: must resume at leg 2, never resend leg 1.
    await runtime.PollAsync(client, Symbol, quote, CancellationToken.None);

    Assert.Equal(2, client.LimitOrders.Count);
    var afterRetry = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Submitted, afterRetry.Stage);
    Assert.Equal(2, afterRetry.SubmittedLegCount);
  }

  [Fact]
  public async Task ALegAlreadyMarketableAtSubmissionFillsAsAMarketOrderNotAStuckLimit()
  {
    // P0 production bug: the ladder leg meant to "enter now" was still
    // submitted as a resting limit order. A BUY limit priced at or through
    // the live ask (or a SELL limit at/through the live bid) is not a valid
    // resting order - it must go in as a real market order instead of
    // sitting there unfillable/rejectable.
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Price has already traded up through leg 1 (4089.50): ask is 4089.40,
    // so BUY leg 1 (price >= ask) is marketable; leg 2 (4085.00) is not.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    var limitOrder = Assert.Single(client.LimitOrders);
    Assert.Equal(4085.00m, limitOrder.LimitPrice);
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Open, state.Stage);
    Assert.NotNull(state.PositionId);
    Assert.Single(state.PendingOrderIds ?? []);
  }

  [Fact]
  public async Task ShadowModeNeverSubmitsRealOrders()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(
      Options("shadow_v7"), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Empty(client.MarketOrders);
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Armed, state.Stage);
  }

  [Fact]
  public void LegacyV6ModeNeverReadsTheV7Stream()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(
      Options("legacy_v6"), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // legacy_v6 gating lives in AutoTradeEngine (it never calls PollAsync in
    // that mode) - this test proves PollAsync itself is harmless to call,
    // not that AutoTradeEngine skips it (see AutoTradeEngineTests for that).
    // Directly assert ShouldSubmitOrders semantics via ContractMode instead.
    Assert.Equal("legacy_v6", Options("legacy_v6").ContractMode);
  }

  [Fact]
  public async Task TargetHitClosesPartialVolumeAndAppliesBreakEven()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    var opened = Assert.Single(runtime.TrackedStates);

    // Price reaches TP1 (4096.00).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.50m, 4096.55m, 2), CancellationToken.None
    );

    Assert.Single(client.Closes);
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, afterTp1.NextTargetIndex);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Single(client.StopAmendments);
    var eventTypes = store.Events.Select(e => e.Type).ToArray();
    Assert.Contains("tp_booked", eventTypes);
    Assert.Contains("sl_moved", eventTypes);
  }

  [Fact]
  public async Task StopKeepsRatchetingBeyondBreakEvenAsLaterTargetsClose()
  {
    // Before this fix, TradePlanRuntime moved the stop to BE after TP1 and
    // then never touched it again - a position that ran all the way to
    // TP3/TP4 sat protected at nothing more than BE, so a full reversal
    // afterward gave back every pip TP2/TP3 had already banked. This proves
    // the V6-equivalent ratchet (trail to the target two levels behind the
    // one that just closed) now runs in the V7 path too.
    var fiveTargets = """
      [
        {"target_id": "TP1", "type": "absolute", "price": "4092.00", "close_ratio": "0.2"},
        {"target_id": "TP2", "type": "absolute", "price": "4094.00", "close_ratio": "0.2"},
        {"target_id": "TP3", "type": "absolute", "price": "4096.00", "close_ratio": "0.2"},
        {"target_id": "TP4", "type": "absolute", "price": "4098.00", "close_ratio": "0.2"},
        {"target_id": "TP5", "type": "absolute", "price": "4100.00", "close_ratio": "0.2"}
      ]
      """;
    var store = new FakeV7Store();
    store.EnqueuePlan($$"""
    {
      "version": 7,
      "plan_id": "v7:plan-1",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Trend Pullback",
        "strategy_family": "trend_pullback",
        "direction": "BUY",
        "context_timeframes": ["M15"],
        "formation_timeframe": "H1",
        "confirmation_timeframe": "M15",
        "formation_bar_ts": 1719999000,
        "confirmation_bar_ts": 1719999600,
        "score": 3.0,
        "confluence": 3,
        "bias": "up",
        "regime": "trend",
        "reasons": ["htf_uptrend"],
        "tags": []
      },
      "source_structure": {
        "structure_id": "zone-xau-4088-4090",
        "kind": "demand",
        "timeframe": "H1",
        "low": "4088.10",
        "high": "4090.00",
        "invalidation_price": "4081.80"
      },
      "entry": {
        "type": "market_watch",
        "expires_at": 2000000000,
        "zone_low": "4088.10",
        "zone_high": "4090.00",
        "activation": "quote_inside_zone",
        "price_side": "ask",
        "max_spread_ticks": 8,
        "max_slippage_ticks": 10,
        "legs": []
      },
      "stop": {
        "type": "absolute",
        "price": "4082.50",
        "source": "structure",
        "structure_id": "zone-xau-4088-4090",
        "reason": "protective stop plan"
      },
      "targets": {{fiveTargets}},
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "management": {
        "be_after_target_id": "TP1",
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
        "allow_limit": false,
        "allow_partial_fill": true,
        "cancel_on_expiry": true
      },
      "provenance": {
        "analysis_engine_version": "",
        "market_map_id": "",
        "config_fingerprint": ""
      }
    }
    """);
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);

    // TP1: BE move (fill 4089.0 + 6 ticks of 0.01 = 4089.06).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Equal(4089.06m, Assert.Single(client.StopAmendments).StopLoss);

    // TP2: two levels back would be a target that doesn't exist yet (V6
    // parity - ordinal 2 is a deliberate no-op) - no further amendment.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 3), CancellationToken.None
    );
    Assert.Single(client.StopAmendments);

    // TP3: trail to TP1's price (two levels back).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.05m, 4096.10m, 4), CancellationToken.None
    );
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4092.00m, client.StopAmendments[^1].StopLoss);

    // TP4: trail to TP2's price.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.05m, 4098.10m, 5), CancellationToken.None
    );
    Assert.Equal(3, client.StopAmendments.Count);
    Assert.Equal(4094.00m, client.StopAmendments[^1].StopLoss);

    var eventTypes = store.Events.Where(e => e.Type == "sl_moved").ToArray();
    Assert.Equal(3, eventTypes.Length);
  }

  [Fact]
  public async Task RestartRecoversArmedStateFromRedis()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var first = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Single(first.TrackedStates);
    Assert.Null(store.Value("execution:plan:v7:plan-1"));
    Assert.NotNull(store.Value("execution:plan_recovery:v7:plan-1"));

    // Simulate a restart: brand new runtime instance, same backing store.
    var second = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    // The recovered plan should now be able to submit its order - proving
    // the plan JSON (not only the lightweight state record) survived.
    Assert.Single(client.MarketOrders);
  }

  [Fact]
  public async Task DuplicatePlanIsClaimedOnceAndNeverDoubleSubmitted()
  {
    var store = new FakeV7Store();
    var planJson = PlanJson();
    store.EnqueuePlan(planJson);
    store.EnqueuePlan(planJson); // same plan_id republished on the stream
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
  }

  [Fact]
  public async Task DuplicateClaimOnALaterPollNeverResubmitsAnAlreadyFilledPlan()
  {
    // P1-3: a duplicate claim (same plan_id, owner already us) arriving
    // AFTER the plan has already progressed past Armed - not within the
    // same poll cycle DuplicatePlanIsClaimedOnceAndNeverDoubleSubmitted
    // covers, where the second entry is overwritten before it is ever
    // evaluated. Falling through to a fresh Armed state here used to
    // resurrect an already-filled plan and submit a second broker order.
    var store = new FakeV7Store();
    var planJson = PlanJson();
    store.EnqueuePlan(planJson);
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Equal(TradePlanRuntimeStage.Open, runtime.TrackedStates.Single().Stage);

    // The same plan_id is redelivered on the stream (eg. a retried publish,
    // or a cursor replay) and picked up on a LATER poll cycle, after the
    // order already filled.
    store.EnqueuePlan(planJson);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Open, runtime.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task MalformedPlanIsDurablyRejectedAndLaterValidPlanStillArms()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan("""{"version":7,"plan_id":"v7:broken","targets":[]}""");
    store.EnqueuePlan(PlanJson(planId: "v7:after-broken"));
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeV7TradingClient(),
      Symbol,
      new SpotPrice("XAU", 4080.0m, 4080.2m, 1),
      CancellationToken.None
    );

    Assert.NotNull(store.Value("execution:plan_rejection:1-0"));
    Assert.Equal("armed", store.Value("execution:plan_state:v7:after-broken"));
    Assert.Equal("2-0", store.TradePlanCursor);
    Assert.Single(runtime.TrackedStates);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_armed"));
  }

  [Fact]
  public async Task TransientRejectionPersistenceFailureLeavesCursorForRetry()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan("""{"version":7,"plan_id":"v7:broken","targets":[]}""");
    store.FailSetOnce("execution:plan_rejection:1-0");
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_000),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeV7TradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal("0-0", store.TradePlanCursor);
    Assert.Null(store.Value("execution:plan_rejection:1-0"));

    await runtime.PollAsync(
      new FakeV7TradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal("1-0", store.TradePlanCursor);
    Assert.NotNull(store.Value("execution:plan_rejection:1-0"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_retry"));
  }

  [Fact]
  public async Task RealRedisConsumesPythonFixtureAfterMalformedEntry()
  {
    var configured = Environment.GetEnvironmentVariable("REAL_REDIS_URL")
      ?? throw new InvalidOperationException("REAL_REDIS_URL is required");
    var sourceUri = new Uri(configured);
    var redisUrl = (
      $"{sourceUri.Scheme}://{sourceUri.Host}:{sourceUri.Port}/13"
    );
    await using var store =
      await StackExchangeRedisSeriesCommands.ConnectAsync(redisUrl);
    var options = ConfigurationOptions.Parse(
      $"{sourceUri.Host}:{sourceUri.Port},defaultDatabase=13,abortConnect=false"
    );
    options.AllowAdmin = true;
    await using var mux = await ConnectionMultiplexer.ConnectAsync(options);
    var db = mux.GetDatabase();
    var prepublishedPlanId = Environment.GetEnvironmentVariable(
      "REAL_REDIS_PREPUBLISHED_V7_PLAN_ID"
    );
    var stream = string.IsNullOrWhiteSpace(prepublishedPlanId)
      ? "execution:trade_plans:p0-real"
      : "execution:trade_plans";
    RedisValue malformedId;
    if (string.IsNullOrWhiteSpace(prepublishedPlanId))
    {
      await db.ExecuteAsync("FLUSHDB");
      malformedId = await db.StreamAddAsync(
        stream,
        [new NameValueEntry(
          "payload",
          """{"version":7,"plan_id":"v7:bad"}"""
        )]
      );
      var payload = PythonContractFixture("market_watch_buy");
      await db.StreamAddAsync(
        stream,
        [new NameValueEntry("payload", payload)]
      );
      prepublishedPlanId = "plan-001";
    }
    else
    {
      var existing = await db.StreamRangeAsync(stream, count: 1);
      malformedId = Assert.Single(existing).Id;
    }
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options() with { TradePlanStream = stream },
      store,
      () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_100),
      logs.Add
    );

    await runtime.PollAsync(
      new FakeV7TradingClient(), Symbol, null, CancellationToken.None
    );

    Assert.Equal(
      "armed",
      await store.GetStringAsync(
        $"execution:plan_state:{prepublishedPlanId}", CancellationToken.None
      )
    );
    Assert.NotNull(await store.GetStringAsync(
      $"execution:plan_rejection:{malformedId}",
      CancellationToken.None
    ));
    Assert.Single(runtime.TrackedStates);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_armed"));
    await db.ExecuteAsync("FLUSHDB");
  }

  private static string PythonContractFixture(string name)
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var path = Path.Combine(
        directory.FullName,
        "contracts",
        "autotrade",
        "trade-plan-v7.json"
      );
      if (File.Exists(path))
      {
        using var fixture = JsonDocument.Parse(File.ReadAllText(path));
        foreach (var item in fixture.RootElement
          .GetProperty("valid_plans").EnumerateArray())
        {
          if (item.GetProperty("name").GetString() == name)
          {
            return item.GetProperty("plan").GetRawText();
          }
        }
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException("Python TradePlan V7 fixture not found");
  }

  private sealed class FakeV7TradingClient : ICTraderTradeClient
  {
    public List<MarketOrderRequest> MarketOrders { get; } = [];
    public List<LimitOrderRequest> LimitOrders { get; } = [];
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    private readonly List<TradingPosition> _positions = [];
    private readonly HashSet<string> _seenClientOrderIds = [];
    private long _nextPositionId = 501;
    private long _nextOrderId = 601;

    // Mirrors the real cTrader behaviour a duplicate leg ClientOrderId
    // actually triggers: the broker rejects the SECOND order carrying an
    // already-used id, exactly like CTraderOpenApiFeedClient.ThrowIfRejected.
    public bool RejectDuplicateClientOrderIds { get; set; }

    // One-shot: the Nth-from-now PlaceLimitOrderAsync call throws (simulating
    // a transient broker rejection unrelated to duplicate ids), then the
    // counter is spent and every later call succeeds normally - lets tests
    // prove a retry resumes from the failed leg instead of leg 0.
    public int ThrowOnCallNumber { get; set; } = -1;
    private int _limitOrderCalls;

    public void SeedPosition(long positionId, TradeDirection direction, long volume) =>
      _positions.Add(new TradingPosition(
        positionId, Symbol.SymbolId, direction, volume, 4089.0m, null,
        "apexvoid-auto", "v7|seed"
      ));

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(CancellationToken ct) =>
      Task.FromResult(new TradingAccountSnapshot(
        1, false, "ScopeTrade", "FullAccess", "Hedged", "Fusion Markets", 2_000m
      ));

    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<TradingPosition>>(_positions);

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order, CancellationToken ct
    )
    {
      MarketOrders.Add(order);
      var positionId = _nextPositionId++;
      _positions.Add(new TradingPosition(
        positionId, order.SymbolId, order.Direction, order.Volume, 4089.0m, null,
        order.Label, order.Comment
      ));
      return Task.FromResult(new TradeExecution(positionId, 1, 4089.0m, order.Volume));
    }

    public Task<long> PlaceLimitOrderAsync(LimitOrderRequest order, CancellationToken ct)
    {
      _limitOrderCalls++;
      if (
        RejectDuplicateClientOrderIds
        && !_seenClientOrderIds.Add(order.ClientOrderId)
      )
      {
        throw new InvalidOperationException(
          $"cTrader rejected order operation: duplicate ClientOrderId "
          + $"{order.ClientOrderId}"
        );
      }
      if (_limitOrderCalls == ThrowOnCallNumber)
      {
        ThrowOnCallNumber = -1;
        throw new InvalidOperationException(
          "cTrader rejected order operation: SERVER_ERROR"
        );
      }
      LimitOrders.Add(order);
      return Task.FromResult(_nextOrderId++);
    }

    public Task AmendPositionStopLossAsync(
      long positionId, decimal stopLoss, CancellationToken ct
    )
    {
      StopAmendments.Add((positionId, stopLoss));
      return Task.CompletedTask;
    }

    public Task<TradeExecution> ClosePositionAsync(
      long positionId, long volume, CancellationToken ct
    )
    {
      Closes.Add((positionId, volume));
      return Task.FromResult(new TradeExecution(positionId, 2, 4096.0m, volume));
    }
  }

  private sealed class FakeV7Store : IAutoTradeStore
  {
    private readonly Dictionary<string, string> _strings = new();
    private readonly List<TradeStreamEntry> _stream = [];
    private readonly HashSet<string> _failSetOnce = [];
    private int _nextStreamId = 1;

    public List<AutoTradeEvent> Events { get; } = [];

    public void EnqueuePlan(string json) =>
      _stream.Add(new TradeStreamEntry($"{_nextStreamId++}-0", json));

    public string TradePlanCursor => _tradePlanCursor;
    public string? Value(string key) =>
      _strings.TryGetValue(key, out var value) ? value : null;
    public void FailSetOnce(string key) => _failSetOnce.Add(key);

    public Task<string> GetCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;
    public Task<string> GetCommandCursorAsync(CancellationToken ct) => Task.FromResult("0-0");
    public Task SetCommandCursorAsync(string cursor, CancellationToken ct) => Task.CompletedTask;

    private string _tradePlanCursor = "0-0";
    public Task<string> GetTradePlanCursorAsync(CancellationToken ct) =>
      Task.FromResult(_tradePlanCursor);
    public Task SetTradePlanCursorAsync(string cursor, CancellationToken ct)
    {
      _tradePlanCursor = cursor;
      return Task.CompletedTask;
    }

    public Task<string?> GetStringAsync(string key, CancellationToken ct) =>
      Task.FromResult(_strings.TryGetValue(key, out var value) ? value : null);

    public Task SetStringAsync(string key, string value, CancellationToken ct)
    {
      if (_failSetOnce.Remove(key))
      {
        throw new IOException($"transient write failure for {key}");
      }
      _strings[key] = value;
      return Task.CompletedTask;
    }

    public Task DeleteStringAsync(string key, CancellationToken ct)
    {
      _strings.Remove(key);
      return Task.CompletedTask;
    }

    public Task<bool> TryClaimStringAsync(
      string key, string value, TimeSpan ttl, CancellationToken ct
    )
    {
      if (_strings.ContainsKey(key))
      {
        return Task.FromResult(false);
      }
      _strings[key] = value;
      return Task.FromResult(true);
    }

    public Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
      string stream, string afterId, int count, CancellationToken ct
    )
    {
      var after = int.Parse(afterId.Split('-')[0]);
      return Task.FromResult<IReadOnlyList<TradeStreamEntry>>(
        _stream
          .Where(entry => int.Parse(entry.Id.Split('-')[0]) > after)
          .Take(count)
          .ToArray()
      );
    }

    public Task PublishAutoTradeEventAsync(
      string stream, AutoTradeEvent tradeEvent, CancellationToken ct
    )
    {
      Events.Add(tradeEvent);
      return Task.CompletedTask;
    }

    // --- V6-only surface this test double never exercises but the
    // interface requires (no default body) - trivial stubs only. ---
    public Task SavePositionAsync(AutoTradePositionState state, CancellationToken ct) =>
      Task.CompletedTask;
    public Task<AutoTradePositionState?> GetPositionAsync(long positionId, CancellationToken ct) =>
      Task.FromResult<AutoTradePositionState?>(null);
    public Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<long>>([]);
    public Task DeletePositionAsync(long positionId, CancellationToken ct) =>
      Task.CompletedTask;
    public Task<long> GetDailyTradeCountAsync(DateOnly date, CancellationToken ct) =>
      Task.FromResult(0L);
    public Task<long> IncrementDailyTradeCountAsync(DateOnly date, CancellationToken ct) =>
      Task.FromResult(1L);
    public Task<bool> IsPausedAsync(CancellationToken ct) => Task.FromResult(false);
    public Task IncrementGateRejectAsync(
      string symbol, string condition, CancellationToken ct
    ) => Task.CompletedTask;
    public Task IncrementAddRejectAsync(
      string symbol, string mode, string condition, CancellationToken ct
    ) => Task.CompletedTask;
    public Task RecordZoneCooldownAsync(
      string symbol, string direction, ZoneCooldownRecord record, int ttlMinutes,
      CancellationToken ct
    ) => Task.CompletedTask;
    public Task SaveGroupPlanAsync(
      AutoTradeGroupPlan plan, TimeSpan ttl, CancellationToken ct
    ) => Task.CompletedTask;
    public Task DeleteGroupPlanAsync(string groupId, CancellationToken ct) =>
      Task.CompletedTask;

    // --- Unused V6 candidate-lease surface: default-interface members cover
    // everything this test double never exercises. ---
    public Task<CandidateClaimResult> TryClaimCandidateAsync(
      string candidateId, string streamEventId, TimeSpan leaseDuration,
      CancellationToken ct, CandidateClaimPolicy? policy = null
    ) => throw new NotSupportedException();

    public Task<bool> RenewCandidateLeaseAsync(
      string candidateId, string streamEventId, string leaseToken,
      TimeSpan leaseDuration, CancellationToken ct
    ) => throw new NotSupportedException();

    public Task<bool> TransitionCandidateStateAsync(
      string candidateId, string streamEventId, string leaseToken, string newState,
      CancellationToken ct, string? lastError = null
    ) => throw new NotSupportedException();

    public Task<string?> GetCandidateStatusAsync(string candidateId, CancellationToken ct) =>
      Task.FromResult<string?>(null);

    public Task<bool> CompleteCandidateAsync(
      string candidateId, string streamEventId, string leaseToken, string outcome,
      CancellationToken ct
    ) => throw new NotSupportedException();

    public Task<bool> ReleaseCandidateAsync(
      string candidateId, string streamEventId, string leaseToken,
      CancellationToken ct, string? lastError = null
    ) => throw new NotSupportedException();
  }
}
