using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

[CollectionDefinition(nameof(EnvMutationCollection), DisableParallelization = true)]
public sealed class EnvMutationCollection;

[Collection(nameof(EnvMutationCollection))]
public sealed class ManifestAuthorityCutoverTests : IDisposable
{
  private readonly Dictionary<string, string?> _snapshot = new(StringComparer.Ordinal);

  private static readonly string[] ManifestOwnedSentinels =
  [
    "AUTO_TRADE_RISK_PCT",
    "AUTO_TRADE_SL_DISTANCE",
    "CTRADER_SYMBOL",
    "CTRADER_TIMEFRAMES",
    "CTRADER_BACKFILL_BARS",
    "BARS_WINDOW_MAX",
    "BARS_CHANNEL",
    "BAR_QUALITY_LOOKBACK",
    "CTRADER_EXPECTED_BROKER",
    "AUTO_TRADE_MAX_SPREAD_PIPS",
    "AUTO_TRADE_TARGETS_PIPS",
  ];

  public ManifestAuthorityCutoverTests()
  {
    SnapshotTouchPoints();
  }

  public void Dispose()
  {
    RestoreSnapshot();
  }

  private void SnapshotTouchPoints()
  {
    foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString();
      if (key is null)
      {
        continue;
      }
      if (
        key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
        || key.StartsWith("CTRADER_", StringComparison.Ordinal)
        || key.StartsWith("BARS_", StringComparison.Ordinal)
        || key.StartsWith("BAR_", StringComparison.Ordinal)
        || key is "REDIS_URL" or "HEALTH_FILE"
      )
      {
        _snapshot[key] = Environment.GetEnvironmentVariable(key);
      }
    }
  }

  private void RestoreSnapshot()
  {
    foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString();
      if (key is null)
      {
        continue;
      }
      if (
        key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
        || key.StartsWith("CTRADER_", StringComparison.Ordinal)
        || key.StartsWith("BARS_", StringComparison.Ordinal)
        || key.StartsWith("BAR_", StringComparison.Ordinal)
        || key is "REDIS_URL" or "HEALTH_FILE"
      )
      {
        if (!_snapshot.ContainsKey(key))
        {
          Environment.SetEnvironmentVariable(key, null);
        }
      }
    }
    foreach (var (key, value) in _snapshot)
    {
      Environment.SetEnvironmentVariable(key, value);
    }
  }

  private static void SetBootstrapEnv()
  {
    Environment.SetEnvironmentVariable("CTRADER_CLIENT_ID", "client");
    Environment.SetEnvironmentVariable("CTRADER_CLIENT_SECRET", "secret");
    Environment.SetEnvironmentVariable("CTRADER_ACCESS_TOKEN", "access");
    Environment.SetEnvironmentVariable("CTRADER_REFRESH_TOKEN", "refresh");
    Environment.SetEnvironmentVariable("CTRADER_ACCOUNT_ID", "42");
    Environment.SetEnvironmentVariable("CTRADER_HOST", "demo.ctraderapi.com");
    Environment.SetEnvironmentVariable("CTRADER_PORT", "5035");
    Environment.SetEnvironmentVariable("REDIS_URL", "redis://localhost:6379/0");
    Environment.SetEnvironmentVariable("HEALTH_FILE", "/tmp/ctrader-feed.heartbeat");
    Environment.SetEnvironmentVariable("CTRADER_REFRESH_TOKEN_KEY", "ctrader:refresh_token");
    Environment.SetEnvironmentVariable(
      "CTRADER_REFRESH_TOKEN_FILE",
      "/tmp/ctrader-token.json"
    );
    Environment.SetEnvironmentVariable("CTRADER_REQUEST_TIMEOUT", "30");
    Environment.SetEnvironmentVariable("CTRADER_TOKEN_REFRESH_LEAD_DAYS", "5");
    Environment.SetEnvironmentVariable("CTRADER_TOKEN_CHECK_INTERVAL_HOURS", "6");
  }

  private static void ClearManifestOwnedEnv()
  {
    foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString() ?? "";
      if (
        key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
        || key.StartsWith("SCANNER_", StringComparison.Ordinal)
        || key is "CTRADER_SYMBOL" or "CTRADER_TIMEFRAMES" or "CTRADER_BACKFILL_BARS"
          or "BARS_WINDOW_MAX" or "BARS_CHANNEL" or "BAR_QUALITY_LOOKBACK"
          or "CTRADER_EXPECTED_BROKER"
      )
      {
        Environment.SetEnvironmentVariable(key, null);
      }
    }
    foreach (var key in ManifestOwnedSentinels)
    {
      Environment.SetEnvironmentVariable(key, null);
    }
  }

  private static string FixturePath()
  {
    var root = Path.GetFullPath(
      Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")
    );
    return Path.Combine(
      root,
      "contracts",
      "configuration",
      "runtime-manifest-example.generated.json"
    );
  }

  [Fact]
  public void Account_bootstrap_from_environment_does_not_require_trading_env()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    var account = CTraderAccountOptions.FromEnvironment();
    Assert.Equal(42, account.AccountId);
    Assert.Equal("demo.ctraderapi.com", account.Host);
    Assert.Equal("redis://localhost:6379/0", account.RedisUrl);
  }

  [Fact]
  public void Manifest_source_ignores_sentinel_trading_env()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    Environment.SetEnvironmentVariable("AUTO_TRADE_RISK_PCT", "999");
    Environment.SetEnvironmentVariable("AUTO_TRADE_SL_DISTANCE", "999");
    Environment.SetEnvironmentVariable("CTRADER_SYMBOL", "WRONG");
    var path = FixturePath();
    Assert.True(File.Exists(path), $"missing fixture {path}");
    var account = CTraderAccountOptions.FromEnvironment();
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    var runtime = ManifestRuntimeFactory.Create(manifest, account);
    Assert.Equal("XAUUSD", runtime.Feed.CTraderSymbol);
    Assert.NotEqual("WRONG", runtime.Feed.CTraderSymbol);
    Assert.NotEqual(999m, runtime.AutoTrade.RiskPercent);
    Assert.NotEqual(999m, runtime.AutoTrade.StopLossDistance);
    Assert.Equal(
      ["EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU"],
      runtime.Instruments.LiveInstruments().Select(i => i.InstrumentId).ToArray()
    );
    Assert.True(runtime.ManifestValidationEnforced);
    Assert.Equal(2, runtime.ManifestVersion);
  }

  [Fact]
  public void Empty_instrument_runtimes_v2_fails_closed()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    var path = FixturePath();
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    var broken = manifest with
    {
      InstrumentRuntimes = new Dictionary<string, System.Text.Json.JsonElement>(),
    };
    var account = CTraderAccountOptions.FromEnvironment();
    var ex = Assert.Throws<InvalidOperationException>(
      () => ManifestRuntimeFactory.Create(broken, account)
    );
    Assert.Contains("instrument_runtimes", ex.Message, StringComparison.Ordinal);
  }

  [Fact]
  public void Missing_bootstrap_credential_fails()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    Environment.SetEnvironmentVariable("CTRADER_CLIENT_SECRET", null);
    Assert.Throws<InvalidOperationException>(CTraderAccountOptions.FromEnvironment);
  }

  [Fact]
  public void Environment_source_still_reads_ctrader_symbol_from_env()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    Environment.SetEnvironmentVariable("CTRADER_SYMBOL", "WRONG");
    var feed = FeedOptions.FromEnvironment();
    Assert.Equal("WRONG", feed.CTraderSymbol);
  }

  [Fact]
  public void Xau_option_differential_env_vs_manifest_is_zero_when_aligned()
  {
    ClearManifestOwnedEnv();
    SetBootstrapEnv();
    var path = FixturePath();
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    var account = CTraderAccountOptions.FromEnvironment();
    var fromManifest = ManifestRuntimeFactory.Create(manifest, account);
    // Build a second identical construction — no ENV trading leakage, diffs must be 0.
    var again = ManifestRuntimeFactory.Create(manifest, account);
    var report = RuntimeManifestParity.Compare(
      fromManifest.Feed,
      fromManifest.AutoTrade,
      again.Feed,
      again.AutoTrade
    );
    Assert.Empty(report.Mismatches);
    var artifact = new
    {
      differences = report.Mismatches.Count,
      live_instruments = fromManifest.Instruments.LiveInstruments()
        .Select(i => i.InstrumentId)
        .ToArray(),
      source = "manifest",
      parity = "off",
      manifest_validation = "enforced",
      candidate_contract_version = fromManifest.AutoTrade.CandidateContractVersion,
      ctrader_symbol = fromManifest.Feed.CTraderSymbol,
      streams = new
      {
        candidates = fromManifest.AutoTrade.CandidateStream,
        events = fromManifest.AutoTrade.EventStream,
        trade_plans = fromManifest.AutoTrade.TradePlanStream,
      },
    };
    var json = System.Text.Json.JsonSerializer.Serialize(artifact);
    Assert.Contains("\"differences\":0", json, StringComparison.Ordinal);
    Assert.Equal(["EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU"], artifact.live_instruments);
  }

  [Fact]
  public void Aot_safe_manifest_loader_reads_fixture()
  {
    var path = FixturePath();
    var manifest = ResolvedRuntimeManifestLoader.Load(path);
    Assert.Equal(2, manifest.ManifestVersion);
    Assert.Contains("XAU", manifest.LiveInstruments);
    Assert.NotNull(manifest.InstrumentRuntimes);
    Assert.True(manifest.InstrumentRuntimes!.ContainsKey("XAU"));
  }
}
