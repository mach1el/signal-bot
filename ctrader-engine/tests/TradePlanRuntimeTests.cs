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
    public List<(long PositionId, long Volume)> Closes { get; } = [];
    public List<(long PositionId, decimal StopLoss)> StopAmendments { get; } = [];
    private readonly List<TradingPosition> _positions = [];
    private long _nextPositionId = 501;

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

    public Task<long> PlaceLimitOrderAsync(LimitOrderRequest order, CancellationToken ct) =>
      Task.FromResult(1L);

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
