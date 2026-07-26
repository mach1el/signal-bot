using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AutoTradeConfigHealthTests
{
  [Fact]
  public void ReversedTargetsSymbolsAliasesAndNumericTokensAreCompatible()
  {
    var current = Manifest();
    var python = PythonManifest(
      rangeTargets: [70, 50, 40, 30, 20, 20],
      symbols: ["XAU", "EURUSD"],
      broker: "fpmarkets-sc",
      accountMode: "demo_required",
      numericDecimals: true
    );

    var health = AutoTradeConfigHealth.Compare(current, python);

    Assert.Equal("healthy", health.State);
    Assert.Empty(health.Fatal);
  }

  [Fact]
  public void MissingRangeTargetRemainsFatal()
  {
    var health = AutoTradeConfigHealth.Compare(
      Manifest(),
      PythonManifest(rangeTargets: [20, 30, 50, 70])
    );

    Assert.Equal("fatal", health.State);
    Assert.Contains("range_target_plans", health.Fatal);
  }

  [Fact]
  public void StorageTtlIsWarningButExecutionAgeIsFatal()
  {
    var warning = AutoTradeConfigHealth.Compare(
      Manifest(),
      PythonManifest(storageTtl: 86400)
    );
    Assert.Equal("healthy", warning.State);
    Assert.Empty(warning.Fatal);
    Assert.Contains("candidate_storage_ttl_seconds", warning.Warnings);

    var fatal = AutoTradeConfigHealth.Compare(
      Manifest(),
      PythonManifest(candidateMaxAge: 90)
    );
    Assert.Equal("fatal", fatal.State);
    Assert.Contains(
      "candidate_execution_max_age_seconds",
      fatal.Fatal
    );
  }

  [Fact]
  public void NonHedgedDemoCapabilityIsWarningOnly()
  {
    var health = AutoTradeConfigHealth.Compare(
      Manifest(hedged: false),
      PythonManifest()
    );

    Assert.Equal("healthy", health.State);
    Assert.Empty(health.Fatal);
    Assert.Contains("broker_non_hedged", health.Warnings);
  }

  private static AutoTradeConfigManifest Manifest(bool hedged = true)
  {
    var options = Options();
    var account = new TradingAccountSnapshot(
      123,
      IsLive: false,
      PermissionScope: "ScopeTrade",
      AccessRights: "FullAccess",
      AccountType: hedged ? "Hedged" : "Netted",
      BrokerName: "FP Markets SC",
      Balance: 2_000m
    );
    var symbol = new SymbolInfo("XAU", "XAUUSD", 41, 2);
    return AutoTradeConfigHealth.Build(options, account, symbol, 1_000);
  }

  private static AutoTradeOptions Options() => new(
    Enabled: true,
    DryRun: false,
    ExpectedBroker: "fpmarkets",
    StopLossDistance: 6.5m,
    TargetsPips: [30, 60, 90, 120, 200],
    TargetWeights: [20, 20, 20, 20, 20],
    BreakEvenBufferPips: 3,
    CandidateMaxAgeSeconds: 420,
    SpotMaxAgeSeconds: 5,
    MaxSpreadPips: 5,
    MaxEntryDistancePips: 10,
    MinConfluence: 2,
    PollMilliseconds: 10,
    CandidateStream: "auto_trade:candidates",
    EventStream: "auto_trade:events",
    Label: "apexvoid-auto",
    Profile: "demo_eval",
    RequireDemoAccount: true,
    RangeTargetsPips: [20, 30, 40, 50, 70],
    RangeTpBufferPips: 3m,
    CandidateStorageTtlSeconds: 604800,
    Symbols: ["EURUSD", "XAU"],
    ZoneFillEnabled: true,
    RangeFlipEnabled: true,
    RangeTwoSidedEnabled: true,
    AllowConcurrentStrategies: true,
    AllowCounterBias: true,
    NonHedgedOppositePolicy: "broker_netting"
  );

  private static string PythonManifest(
    IReadOnlyList<int>? rangeTargets = null,
    IReadOnlyList<string>? symbols = null,
    string broker = "fpmarkets",
    string accountMode = "demo",
    bool numericDecimals = false,
    int storageTtl = 604800,
    int candidateMaxAge = 420
  )
  {
    var current = Manifest();
    var payload = new Dictionary<string, object?>
    {
      ["config_manifest_version"] = numericDecimals ? 2.0m : 2,
      ["auto_trade_enabled"] = true,
      ["dry_run"] = false,
      ["candidate_stream"] = current.CandidateStream,
      ["event_stream"] = current.EventStream,
      ["redis_database"] = current.RedisDatabase,
      ["redis_fingerprint"] = current.RedisFingerprint,
      ["symbols"] = symbols ?? ["EURUSD", "XAU"],
      ["canonical_symbol"] = "xau",
      ["pip_size"] = 0.1m,
      ["contract_size"] = numericDecimals ? 100.0m : 100m,
      ["candidate_contract_version"] = numericDecimals ? 6.0m : 6,
      ["structure_stop_buffer_atr"] = 0.3m,
      ["ordinary_stop_min_pips"] = 30,
      ["ordinary_stop_max_distance"] = 6.5m,
      ["wick_stop_buffer_atr"] = 0.15m,
      ["trend_stop_min_pips"] = 40,
      ["trend_stop_max_pips"] = 65,
      ["target_plans"] = new[] { 200, 120, 90, 60, 30 },
      ["range_target_plans"] = rangeTargets ?? [20, 30, 40, 50, 70],
      ["range_tp_buffer"] = numericDecimals ? 3.0m : 3m,
      ["candidate_execution_max_age_seconds"] = candidateMaxAge,
      ["candidate_storage_ttl_seconds"] = storageTtl,
      ["spot_max_age_seconds"] = 5,
      ["require_demo_account"] = true,
      ["profile"] = "demo_eval",
      ["account_mode"] = accountMode,
      ["broker"] = broker,
      ["broker_hedging_capability"] = true,
      ["zone_fill"] = true,
      ["range_flip"] = true,
      ["two_sided_range"] = true,
      ["concurrent_strategies"] = true,
      ["allow_counter_bias"] = true,
      ["min_confluence"] = 2,
      ["non_hedged_opposite_policy"] = "broker_netting",
      ["git_sha"] = "test",
    };
    return JsonSerializer.Serialize(payload);
  }
}
