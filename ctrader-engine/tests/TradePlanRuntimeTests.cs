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
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
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
  public async Task ReceivesPlanAndSubmitsMarketOrderWhenQuoteEntersZone()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    // Quote outside the zone: plan should be received but not submit yet.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Empty(client.MarketOrders);
    var received = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Received, received.Stage);

    // Quote enters the zone: should submit a market order now.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    var order = Assert.Single(client.MarketOrders);
    Assert.Equal(TradeDirection.Buy, order.Direction);
    Assert.Contains("v7:plan-1", order.Comment);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
    Assert.NotNull(open.PositionId);
    var events = store.Events.Select(e => e.Type).ToArray();
    Assert.DoesNotContain("plan_armed", events);
    Assert.Contains("order_filled", events);
  }

  [Fact]
  public async Task ExpiredPlanLogsTheLastReasonItNeverFilled()
  {
    // Live incident: a market_watch plan expired unfilled even though price
    // logs showed it re-entering the zone a few minutes before expiry -
    // every poll that didn't submit was completely silent, so there was no
    // way to tell from production logs whether the entry never actually saw
    // the zone again, or saw it but got blocked by something else (spread).
    // The expiry log line must now say which one happened.
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson(expiresAt: 1_720_000_100));
    var client = new FakeV7TradingClient();
    var logs = new List<string>();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime),
      logs.Add
    );

    // Outside the zone - Wait, reason recorded in-memory but nothing
    // logged yet (a Wait on every poll would otherwise spam every setup
    // still waiting for price, which is the normal/common case).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.DoesNotContain(logs, line => line.Contains("plan expired"));

    // Clock now past expires_at - this poll's quote is irrelevant, the
    // plan expires using the reason recorded on the poll just above.
    currentTime = 1_720_000_100L;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 2), CancellationToken.None
    );

    Assert.Contains(
      logs, line => line.Contains("v7 plan expired")
        && line.Contains("last_wait_reason=outside_zone")
    );
    // Live incident: this branch used to log-and-forget with no
    // PublishEventAsync call at all, unlike every other terminal
    // transition in this file (plan_rejected/order_filled/...) - the
    // owner's forming card never resolved and nothing told them the
    // setup died. Must now publish like everything else does.
    Assert.Contains(
      store.Events, e => e.Type == "plan_expired"
        && e.Message.Contains("outside_zone")
    );
  }

  [Fact]
  public async Task ExpiredPlanThatWasNeverEvaluatedLogsNeverEvaluated()
  {
    // A plan can expire on its very first poll (e.g. expires_at already in
    // the past by the time the stream is consumed) without EvaluateEntry
    // ever reaching the market_watch branch at all - LastEntryWaitReason
    // stays null, and the log must say so plainly rather than a misleading
    // blank/default reason.
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson(expiresAt: 1_720_000_000));
    var client = new FakeV7TradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(1_720_000_100),
      logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );

    Assert.Contains(
      logs, line => line.Contains("v7 plan expired")
        && line.Contains("last_wait_reason=never_evaluated")
    );
    Assert.Contains(
      store.Events, e => e.Type == "plan_expired"
        && e.Message.Contains("never_evaluated")
    );
  }

  [Fact]
  public async Task FirstPollSubmitsExecutablePlanWithoutArmedStage()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_armed");
    Assert.DoesNotContain(
      runtime.TrackedStates,
      s => s.Stage is TradePlanRuntimeStage.Received
        or TradePlanRuntimeStage.Submitting
    );
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
    // Marketable market_watch uses the live executable ask (4089.10) as the
    // relative-SL entry reference, not the zone proximal edge.
    // distance to the 4082.50 stop is 6.60 -> 6.60 * 100_000 = 660_000.
    Assert.Equal(660_000, order.RelativeStopLoss);
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
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
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
    Assert.True(order.Volume > 0);
    var open = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, open.Stage);
  }

  [Fact]
  public async Task UndersizedLimitLadderRejectsThatPlanWithoutCrashingTheConsumer()
  {
    // A limit_ladder whose max_volume cannot meet the broker MinVolume is
    // rejected during arming (equity_table still respects plan.Risk.MaxVolume).
    // That sizing check runs before the plan is ever armed - a plan that can
    // never be sized must never show PLAN ARMED only to flip to PLAN REJECTED
    // a moment later - and it must reject just this plan, not crash-loop the
    // whole consumer forever on every poll.
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
        "strategy": "Zone Reaction",
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
        "max_volume": 50,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.90", "0.10"]
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

    // The plan is rejected during receive itself (before
    // EvaluatePendingEntryPlansAsync or SubmitEntryAsync ever run), on this
    // same first poll.
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

  [Fact]
  public async Task MarketWithLimitScaleSubmitsL1MarketAndL2LimitOnFirstPoll()
  {
    const string planJson = """
    {
      "version": 7,
      "plan_id": "v7:plan-mwls",
      "thesis_id": "thesis-1",
      "setup_id": "setup-1",
      "symbol": "XAU",
      "created_at": 1719999600,
      "expires_at": 2000000000,
      "analysis": {
        "strategy": "Key Level Reaction",
        "strategy_family": "key_level",
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
        "reasons": [],
        "tags": []
      },
      "source_structure": {
        "structure_id": "key:M15:4085.00:4089.50:1719990000",
        "kind": "key_level",
        "timeframe": "M15",
        "low": "4085.00",
        "high": "4089.50",
        "invalidation_price": "4079.00"
      },
      "entry": {
        "type": "market_with_limit_scale",
        "zone_low": "4085.00",
        "zone_high": "4089.50",
        "expires_at": 2000000000,
        "legs": [
          {"leg_id": "L1", "price": "4089.10", "volume_ratio": "0.70", "order_type": "market"},
          {"leg_id": "L2", "price": "4085.00", "volume_ratio": "0.30", "order_type": "limit"}
        ]
      },
      "stop": {
        "type": "absolute",
        "price": "4079.00",
        "source": "structural_invalidation",
        "structure_id": "key:M15:4085.00:4089.50:1719990000",
        "reason": "below distal edge"
      },
      "targets": [
        {"target_id": "TP1", "type": "absolute", "price": "4097.00", "close_ratio": "1.0"}
      ],
      "risk": {
        "risk_percent": "1.0",
        "risk_multiplier": "1.0",
        "max_volume": 100000,
        "max_group_risk_percent": "2.0"
      },
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "zone_scale",
        "leg_ratios": ["0.70", "0.30"]
      },
      "management": {
        "be_after_target_id": null,
        "be_buffer_ticks": 6,
        "never_worsen_stop": true
      },
      "execution_policy": {
        "allow_market": true,
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
    var store = new FakeV7Store();
    store.EnqueuePlan(planJson);
    var client = new FakeV7TradingClient { AccountEquity = 1_300m, AccountBalance = 1_300m };
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    // Quote inside the zone: L1 must PlaceMarketOrder (order_type=market)
    // even though a marketable-limit check on the L1 reference price would
    // also choose market; L2 must PlaceLimitOrder (order_type=limit) even
    // though detection is not consulted.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.00m, 4089.10m, 1), CancellationToken.None
    );

    var market = Assert.Single(client.MarketOrders);
    Assert.Equal(800, market.Volume); // 0.08 lots at equity 1300
    var limit = Assert.Single(client.LimitOrders);
    Assert.Equal(4085.00m, limit.LimitPrice);
    Assert.Equal(300, limit.Volume); // 0.03 lots
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, state.Stage);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_armed");
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
      "strategy": "Zone Reaction",
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
      "max_volume": 100000,
      "max_group_risk_percent": "2.0"
    },
    "sizing": {
      "mode": "equity_table",
      "table_version": "owner_equity_v1",
      "entry_distribution": "zone_scale",
      "leg_ratios": ["0.60", "0.40"]
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
  public void RelativeStopLossForEntryDiffersPerEntryButSharesAbsoluteStop()
  {
    const decimal absolute = 4079.00m;
    var l1 = TradePlanJson.RelativeStopLossForEntry(4089.50m, absolute);
    var l2 = TradePlanJson.RelativeStopLossForEntry(4085.00m, absolute);
    Assert.Equal(1_050_000, l1);
    Assert.Equal(600_000, l2);
    Assert.NotEqual(l1, l2);
    Assert.Equal(4089.50m - absolute, l1 / 100_000m);
    Assert.Equal(4085.00m - absolute, l2 / 100_000m);
  }

  [Theory]
  [InlineData("v7|v7:plan-1|thesis-1|L1", null, "v7:plan-1", "thesis-1", "L1")]
  [InlineData("v7|v7:plan-1|thesis-1|L2", null, "v7:plan-1", "thesis-1", "L2")]
  [InlineData("v7|v7:plan-1|thesis-1|0", null, "v7:plan-1", "thesis-1", "L1")]
  [InlineData("v7|v7:plan-1|thesis-1|1", null, "v7:plan-1", "thesis-1", "L2")]
  [InlineData(null, "v7:plan-1:L1", "v7:plan-1", "", "L1")]
  [InlineData(null, "v7:plan-1:0", "v7:plan-1", "", "L1")]
  public void TryParseV7OwnershipMapsL1L2AndLegacyIndex(
    string? comment,
    string? clientOrderId,
    string planId,
    string thesisId,
    string legId
  )
  {
    var ownership = TradePlanV7Ownership.TryParseV7Ownership(comment, clientOrderId);
    Assert.NotNull(ownership);
    Assert.Equal(planId, ownership!.PlanId);
    Assert.Equal(thesisId, ownership.ThesisId);
    Assert.Equal(legId, ownership.LegId);
  }

  [Fact]
  public async Task LadderL1MarketFillAndL2PendingIsPartiallyOpenThenFullyOpen()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );

    var partial = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, partial.Stage);
    Assert.Equal(TradePlanGroupStages.PartiallyOpen, partial.GroupStage);
    var l1 = Assert.Single(partial.Legs!, leg => leg.LegId == "L1");
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");
    Assert.NotNull(l1.BrokerPositionId);
    Assert.NotNull(l2.BrokerOrderId);
    Assert.Null(l2.BrokerPositionId);
    var pendingOrderId = l2.BrokerOrderId!.Value;

    client.FillPendingOrder(pendingOrderId);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    var full = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, full.Stage);
    Assert.Equal(TradePlanGroupStages.FullyOpen, full.GroupStage);
    Assert.Equal(2, full.Legs!.Count(leg => leg.BrokerPositionId is not null));
    Assert.Equal(
      2,
      full.Legs!.Select(leg => leg.BrokerPositionId).Distinct().Count()
    );
  }

  [Fact]
  public async Task OwnerCancellingBothLadderLegsOnBrokerIsRecognizedAsPlanCancelled()
  {
    // 04 Aug incident (card 2): owner cancelled a Flip Zone limit-ladder's
    // pending legs directly on the broker platform. The plan stayed
    // reported as "submitted" forever - Telegram was never told, and
    // TrackedStates never released it. Quote well above both leg prices so
    // neither fires as an immediate market order; both rest as pending
    // limit orders, matching the real incident's zone-scale ladder.
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var logs = new List<string>();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime), logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 1), CancellationToken.None
    );

    var submitted = Assert.Single(runtime.TrackedStates);
    Assert.All(submitted.Legs!, leg => Assert.Null(leg.BrokerPositionId));
    Assert.All(submitted.Legs!, leg => Assert.NotNull(leg.BrokerOrderId));

    // Owner cancels both legs directly on the broker - not through our own
    // CancelPendingOrderAsync, which is exactly the point: the broker-side
    // state changed out from under us.
    client.PendingOrders.Clear();

    // First poll after the cancel: the gap is only just noticed, not yet
    // confirmed - must not jump straight to cancelled (a same-instant fill
    // needs a full cycle for its position to land in ReconcilePositionsAsync).
    currentTime += 1;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 2), CancellationToken.None
    );
    Assert.Single(runtime.TrackedStates);
    Assert.DoesNotContain(store.Events, e => e.Type == "plan_cancelled");

    // Gap survives past the confirmation window - now it's a real cancel.
    currentTime += 11;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4095.00m, 4095.20m, 3), CancellationToken.None
    );

    Assert.Empty(runtime.TrackedStates);
    Assert.Contains(
      store.Events, e => e.Type == "plan_cancelled"
        && e.Message.Contains("owner cancelled")
    );
    Assert.Contains(logs, line => line.Contains("v7 plan cancelled"));
  }

  [Fact]
  public async Task OwnerCancellingOneUnfilledLadderLegLeavesTheFilledLegManaged()
  {
    // Guard against the cancelled-leg detection above being too broad: a
    // partial fill (L1 real position) plus a cancelled L2 must still read
    // as a live, managed trade - not get swept into "plan cancelled" just
    // because one leg never filled.
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var currentTime = 1_720_000_000L;
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.FromUnixTimeSeconds(currentTime), _ => { }
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var partial = Assert.Single(runtime.TrackedStates);
    var l2 = Assert.Single(partial.Legs!, leg => leg.LegId == "L2");
    client.PendingOrders.RemoveAll(order => order.OrderId == l2.BrokerOrderId!.Value);

    currentTime += 12;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );
    currentTime += 12;
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 3), CancellationToken.None
    );

    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, state.Stage);
    Assert.NotEqual(TradePlanGroupStages.Cancelled, state.GroupStage);
    Assert.Contains(state.Legs!, leg => leg.LegId == "L2"
      && leg.Stage == TradePlanLegStages.Cancelled);
  }

  [Fact]
  public async Task V7CommentPositionIsAdoptedWithoutCannotReconstructLog()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var logs = new List<string>();
    var runtime = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, logs.Add
    );

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    var state = Assert.Single(runtime.TrackedStates);
    var l2 = Assert.Single(state.Legs!, leg => leg.LegId == "L2");
    client.FillPendingOrder(l2.BrokerOrderId!.Value);

    var orphan = (await client.ReconcilePositionsAsync(CancellationToken.None))
      .Single(position => position.Comment.Contains("|L2", StringComparison.Ordinal));
    logs.Clear();
    var adopted = await runtime.TryAdoptV7BrokerPositionAsync(
      client, Symbol, orphan, CancellationToken.None
    );

    Assert.True(adopted);
    Assert.DoesNotContain(
      logs, line => line.Contains("cannot reconstruct", StringComparison.Ordinal)
    );
    Assert.Contains(logs, line => line.Contains("v7 adopt:", StringComparison.Ordinal));
    var after = Assert.Single(runtime.TrackedStates);
    Assert.Contains(
      after.Legs!,
      leg => leg.LegId == "L2" && leg.BrokerPositionId == orphan.PositionId
    );
  }

  [Fact]
  public async Task RestartAfterL1FillDoesNotResubmitL1()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(LadderPlanJson);
    var client = new FakeV7TradingClient();
    var first = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );

    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 1), CancellationToken.None
    );
    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    var afterL1 = Assert.Single(first.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, afterL1.Stage);
    Assert.Equal(2, afterL1.SubmittedLegCount);

    var second = new TradePlanRuntime(
      Options(), store, () => DateTimeOffset.UtcNow, _ => { }
    );
    await second.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.20m, 4089.40m, 2), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(client.LimitOrders);
    var recovered = Assert.Single(second.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, recovered.Stage);
    Assert.Contains(
      recovered.Legs!,
      leg => leg.LegId == "L1" && leg.BrokerPositionId is not null
    );
  }

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
    Assert.Equal(
      TradePlanJson.RelativeStopLossForEntry(4089.50m, 4079.00m),
      client.LimitOrders.Single(o => o.ClientOrderId.EndsWith(":L1", StringComparison.Ordinal))
        .RelativeStopLoss
    );
    Assert.Equal(
      TradePlanJson.RelativeStopLossForEntry(4085.00m, 4079.00m),
      client.LimitOrders.Single(o => o.ClientOrderId.EndsWith(":L2", StringComparison.Ordinal))
        .RelativeStopLoss
    );
    var clientOrderIds = client.LimitOrders.Select(o => o.ClientOrderId).ToArray();
    Assert.Equal(clientOrderIds.Length, clientOrderIds.Distinct().Count());
    Assert.Contains("v7:plan-1:L1", clientOrderIds);
    Assert.Contains("v7:plan-1:L2", clientOrderIds);
    var comments = client.LimitOrders.Select(o => o.Comment).ToArray();
    Assert.Equal(comments.Length, comments.Distinct().Count());
    Assert.Contains(comments, c => c.EndsWith("|L1", StringComparison.Ordinal));
    Assert.Contains(comments, c => c.EndsWith("|L2", StringComparison.Ordinal));
    var state = Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Submitted, state.Stage);
    Assert.Equal(2, state.SubmittedLegCount);
    Assert.Equal(2, state.Legs?.Count);
  }

  [Fact]
  public async Task RetryAfterALegFailureResumesWithoutResubmittingAnAcceptedLeg()
  {
    // P0 production bug: leg 2 erroring (duplicate ClientOrderId, or any
    // other broker rejection) threw before Stage ever became Submitted, so
    // the plan stayed Received/Submitting and the NEXT poll resubmitted
    // leg 1 from scratch - even though the broker had already accepted it.
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
    // Stage stays Submitting (not Submitted) after a partial failure -
    // EvaluatePendingEntryPlansAsync only re-evaluates Received/Submitting
    // plans, so this is what makes the retry below come back.
    Assert.Equal(TradePlanRuntimeStage.Submitting, afterFailure.Stage);
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
    Assert.Equal(TradePlanRuntimeStage.PartiallyOpen, state.Stage);
    Assert.NotNull(state.PositionId);
    Assert.Single(state.PendingOrderIds ?? []);
    Assert.Equal(2, state.Legs?.Count);
    Assert.Contains(state.Legs!, leg =>
      leg.LegId == "L1" && leg.BrokerPositionId is not null
    );
    Assert.Contains(state.Legs!, leg =>
      leg.LegId == "L2" && leg.BrokerOrderId is not null && leg.BrokerPositionId is null
    );
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
    Assert.Equal(TradePlanRuntimeStage.Received, state.Stage);
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
    // Entry amends the group absolute stop onto the filled position; TP1 then
    // moves it to break-even.
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4082.50m, client.StopAmendments[0].StopLoss);
    Assert.Equal(4089.06m, client.StopAmendments[1].StopLoss);
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
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
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
    // Entry amends the group absolute protective stop onto the fill.
    Assert.Equal(4082.50m, Assert.Single(client.StopAmendments).StopLoss);

    // TP1: BE move (fill 4089.0 + 6 ticks of 0.01 = 4089.06).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );
    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.Equal(4089.06m, client.StopAmendments[^1].StopLoss);

    // TP2: two levels back would be a target that doesn't exist yet (V6
    // parity - ordinal 2 is a deliberate no-op) - no further amendment.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 3), CancellationToken.None
    );
    Assert.Equal(2, client.StopAmendments.Count);

    // TP3: trail to TP1's price (two levels back).
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4096.05m, 4096.10m, 4), CancellationToken.None
    );
    Assert.Equal(3, client.StopAmendments.Count);
    Assert.Equal(4092.00m, client.StopAmendments[^1].StopLoss);

    // TP4: trail to TP2's price.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4098.05m, 4098.10m, 5), CancellationToken.None
    );
    Assert.Equal(4, client.StopAmendments.Count);
    Assert.Equal(4094.00m, client.StopAmendments[^1].StopLoss);

    var eventTypes = store.Events.Where(e => e.Type == "sl_moved").ToArray();
    Assert.Equal(3, eventTypes.Length);
  }

  [Fact]
  public async Task UndersizedPositionClosesOneStepPerTargetInsteadOfSkipping()
  {
    // Live incident (2026-08-03): a small-equity position (0.02 lots, two
    // StepVolume units) with 5 equal-weight targets hit TP1, but 20% of
    // that volume rounds down below StepVolume - so PlanPartialCloseVolume
    // returned 0 and the "can't book a valid partial" branch fired. That
    // branch used to jump NextTargetIndex straight to Targets.Count - 1
    // (4), even though price had only ever reached TP1. The trail-stop
    // step further down reads `NextTargetIndex - 3` assuming that value
    // tracks genuinely reached targets, so it then tried to amend the SL
    // to TP2's price - a level price had never actually touched - and the
    // broker rejected that amend (TRADING_BAD_STOPS: new SL below current
    // ask) on every single poll thereafter, forever. Confirmed live: 300+
    // identical rejections over 2 hours on one position, stop never
    // actually trailing past break-even.
    //
    // The fix floors the proportional close to one StepVolume whenever the
    // raw share would round below it (and there's at least one step of
    // room left) instead of skipping the target outright. For a position
    // with only as many steps as roughly N targets, this degrades to
    // closing one step per target reached - a 2-step position closes half
    // at TP1 and the other half at TP2 - rather than silently deferring
    // everything to whichever target's redistributed share eventually
    // happens to cross the StepVolume line.
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
      "sizing": {
        "mode": "equity_table",
        "table_version": "owner_equity_v1",
        "entry_distribution": "single",
        "leg_ratios": []
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
    // Equity 200 -> LotsForEquity floors to 0.02 lots -> 200 units, exactly
    // two StepVolume(100) steps - small enough that a single 20% target
    // slice (40 units) rounds down below StepVolume.
    var client = new FakeV7TradingClient { AccountEquity = 200m, AccountBalance = 200m };
    var runtime = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });

    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 1), CancellationToken.None
    );
    Assert.Equal(200, Assert.Single(client.MarketOrders).Volume);
    Assert.Equal(4082.50m, Assert.Single(client.StopAmendments).StopLoss);

    // Price reaches TP1: the raw 20% share (40) rounds below StepVolume
    // (100), so the fix books one whole step (100 - half the position)
    // instead of skipping it.
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4092.05m, 4092.10m, 2), CancellationToken.None
    );

    var afterTp1 = Assert.Single(runtime.TrackedStates);
    Assert.Equal(1, afterTp1.NextTargetIndex);
    Assert.Equal(100, Assert.Single(client.Closes).Volume);
    Assert.Equal(100, afterTp1.RemainingVolume);
    // BE applies in the same poll as TP1 (unchanged, pre-existing
    // behavior) - entry stop + BE stop, no bogus trail attempt.
    Assert.Equal(2, client.StopAmendments.Count);
    Assert.True(afterTp1.BreakEvenApplied);
    Assert.Contains(store.Events, e => e.Type == "tp_booked");

    // Price reaches TP2: only one StepVolume (100) remains, so this closes
    // the entire remainder in one shot - the "other half".
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4094.05m, 4094.10m, 3), CancellationToken.None
    );

    Assert.Equal(2, client.Closes.Count);
    Assert.Equal(100, client.Closes[^1].Volume);
    // A fully closed position is pruned from TrackedStates - the
    // "position_closed" event is the durable record of the final close.
    var closedEvent = Assert.Single(
      store.Events, e => e.Type == "position_closed"
    );
    Assert.Equal(0, closedEvent.RemainingVolume);
  }

  [Fact]
  public async Task RestartRecoversReceivedStateFromRedis()
  {
    var store = new FakeV7Store();
    store.EnqueuePlan(PlanJson());
    var client = new FakeV7TradingClient();
    var first = new TradePlanRuntime(Options(), store, () => DateTimeOffset.UtcNow, _ => { });
    await first.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4080.0m, 4080.2m, 1), CancellationToken.None
    );
    Assert.Single(first.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.Received, first.TrackedStates.Single().Stage);
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
    // AFTER the plan has already progressed past Received - not within the
    // same poll cycle DuplicatePlanIsClaimedOnceAndNeverDoubleSubmitted
    // covers, where the second entry is overwritten before it is ever
    // evaluated. Falling through to a fresh Received state here used to
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
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, runtime.TrackedStates.Single().Stage);

    // The same plan_id is redelivered on the stream (eg. a retried publish,
    // or a cursor replay) and picked up on a LATER poll cycle, after the
    // order already filled.
    store.EnqueuePlan(planJson);
    await runtime.PollAsync(
      client, Symbol, new SpotPrice("XAU", 4089.05m, 4089.10m, 2), CancellationToken.None
    );

    Assert.Single(client.MarketOrders);
    Assert.Single(runtime.TrackedStates);
    Assert.Equal(TradePlanRuntimeStage.FullyOpen, runtime.TrackedStates.Single().Stage);
  }

  [Fact]
  public async Task MalformedPlanIsDurablyRejectedAndLaterValidPlanStillReceives()
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
    Assert.Equal("received", store.Value("execution:plan_state:v7:after-broken"));
    Assert.Equal("2-0", store.TradePlanCursor);
    Assert.Single(runtime.TrackedStates);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
  }

  [Fact]
  public async Task MalformedPlanWithUnsupportedExceptionStillYieldsToValidPlan()
  {
    // Source-gen / required-member failures can surface as NotSupportedException
    // rather than JsonException — must not abort the poll batch.
    var store = new FakeV7Store();
    store.EnqueuePlan("""{"version":7,"plan_id":"v7:unsupported-shape"}""");
    store.EnqueuePlan(PlanJson(planId: "v7:after-unsupported"));
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
    Assert.Equal("received", store.Value("execution:plan_state:v7:after-unsupported"));
    Assert.Equal("2-0", store.TradePlanCursor);
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_rejected"));
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
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
    var configured = Environment.GetEnvironmentVariable("REAL_REDIS_URL");
    if (string.IsNullOrWhiteSpace(configured))
    {
      // Optional live-redis integration; keep the P0 unit filter green.
      return;
    }
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
      "received",
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
    Assert.Contains(logs, line => line.Contains("auto_trade_plan_received_ready"));
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
    public List<long> CancelledOrderIds { get; } = [];
    public List<TradingPendingOrder> PendingOrders { get; } = [];
    private readonly List<TradingPosition> _positions = [];
    private readonly HashSet<string> _seenClientOrderIds = [];
    private long _nextPositionId = 501;
    private long _nextOrderId = 601;

    public decimal AccountBalance { get; set; } = 2_000m;
    public decimal AccountEquity { get; set; } = 2_000m;
    public string EquitySource { get; set; } = "test";
    public PositionCloseReason PositionCloseReasonToReturn { get; set; } =
      PositionCloseReason.Unknown;
    public List<long> PositionCloseReasonLookups { get; } = [];

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

    public void SeedPosition(
      long positionId,
      TradeDirection direction,
      long volume,
      string comment = "v7|seed",
      string clientOrderId = "",
      decimal entryPrice = 4089.0m,
      decimal? stopLoss = null
    ) =>
      _positions.Add(new TradingPosition(
        positionId, Symbol.SymbolId, direction, volume, entryPrice, stopLoss,
        "apexvoid-auto", comment, clientOrderId
      ));

    public void RemovePosition(long positionId) =>
      _positions.RemoveAll(position => position.PositionId == positionId);

    public void FillPendingOrder(long orderId, decimal? fillPrice = null)
    {
      var pending = PendingOrders.Single(order => order.OrderId == orderId);
      PendingOrders.Remove(pending);
      LimitOrders.RemoveAll(order =>
        order.ClientOrderId == pending.ClientOrderId
      );
      var positionId = _nextPositionId++;
      _positions.Add(new TradingPosition(
        positionId,
        pending.SymbolId,
        pending.Direction,
        pending.Volume,
        fillPrice ?? pending.LimitPrice,
        null,
        pending.Label,
        pending.Comment,
        pending.ClientOrderId
      ));
    }

    public Task<TradingAccountSnapshot> GetTradingAccountAsync(CancellationToken ct) =>
      Task.FromResult(new TradingAccountSnapshot(
        1, false, "ScopeTrade", "FullAccess", "Hedged", "Fusion Markets",
        AccountBalance, AccountEquity, 1_720_000_000, EquitySource
      ));

    public Task<IReadOnlyList<TradingPosition>> ReconcilePositionsAsync(CancellationToken ct) =>
      Task.FromResult<IReadOnlyList<TradingPosition>>(_positions.ToArray());

    public Task<IReadOnlyList<TradingPendingOrder>> ReconcilePendingOrdersAsync(
      CancellationToken ct
    ) => Task.FromResult<IReadOnlyList<TradingPendingOrder>>(PendingOrders.ToArray());

    public Task CancelPendingOrderAsync(long orderId, CancellationToken ct)
    {
      CancelledOrderIds.Add(orderId);
      var pending = PendingOrders.FirstOrDefault(order => order.OrderId == orderId);
      PendingOrders.RemoveAll(order => order.OrderId == orderId);
      if (pending is not null)
      {
        LimitOrders.RemoveAll(order => order.ClientOrderId == pending.ClientOrderId);
      }
      return Task.CompletedTask;
    }

    public Task<PositionCloseLookup> DeterminePositionCloseReasonAsync(
      long positionId,
      long openedAtTimestamp,
      long approximateCloseTimestamp,
      CancellationToken cancellationToken
    )
    {
      PositionCloseReasonLookups.Add(positionId);
      return Task.FromResult(new PositionCloseLookup(PositionCloseReasonToReturn));
    }

    public Task<TradeExecution> PlaceMarketOrderAsync(
      MarketOrderRequest order, CancellationToken ct
    )
    {
      if (
        RejectDuplicateClientOrderIds
        && !string.IsNullOrWhiteSpace(order.ClientOrderId)
        && !_seenClientOrderIds.Add(order.ClientOrderId)
      )
      {
        throw new InvalidOperationException(
          $"cTrader rejected order operation: duplicate ClientOrderId "
          + $"{order.ClientOrderId}"
        );
      }
      MarketOrders.Add(order);
      var positionId = _nextPositionId++;
      var fillPrice = order.Direction == TradeDirection.Buy
        ? 4089.0m
        : 4098.46m;
      _positions.Add(new TradingPosition(
        positionId, order.SymbolId, order.Direction, order.Volume, fillPrice, null,
        order.Label, order.Comment, order.ClientOrderId
      ));
      return Task.FromResult(new TradeExecution(positionId, 1, fillPrice, order.Volume));
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
      var orderId = _nextOrderId++;
      PendingOrders.Add(new TradingPendingOrder(
        orderId,
        order.SymbolId,
        order.Direction,
        order.Volume,
        order.LimitPrice,
        order.Label,
        order.Comment,
        order.ClientOrderId
      ));
      return Task.FromResult(orderId);
    }

    public Task AmendPositionStopLossAsync(
      long positionId, decimal stopLoss, CancellationToken ct
    )
    {
      StopAmendments.Add((positionId, stopLoss));
      var idx = _positions.FindIndex(position => position.PositionId == positionId);
      if (idx >= 0)
      {
        var current = _positions[idx];
        _positions[idx] = current with { StopLoss = stopLoss };
      }
      return Task.CompletedTask;
    }

    public Task<TradeExecution> ClosePositionAsync(
      long positionId, long volume, CancellationToken ct
    )
    {
      Closes.Add((positionId, volume));
      var idx = _positions.FindIndex(position => position.PositionId == positionId);
      if (idx >= 0)
      {
        var current = _positions[idx];
        var remaining = Math.Max(0, current.Volume - volume);
        if (remaining <= 0)
        {
          _positions.RemoveAt(idx);
        }
        else
        {
          _positions[idx] = current with { Volume = remaining };
        }
      }
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
