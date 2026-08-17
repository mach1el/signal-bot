using System.Collections;
using System.Globalization;
using System.Reflection;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

[Collection(nameof(EnvMutationCollection))]
public sealed class ManifestAuthorityLegacyDifferentialTests
{
  [Fact]
  public void Legacy_environment_and_manifest_xau_options_have_zero_differences()
  {
    var snapshot = new Dictionary<string, string?>(StringComparer.Ordinal);
    try
    {
      ClearConfigurationEnvironment(snapshot);
      SeedAccountBootstrap(snapshot);

      var root = RepositoryRoot();
      var manifest = ResolvedRuntimeManifestLoader.Load(
        Path.Combine(
          root,
          "contracts",
          "configuration",
          "runtime-manifest-example.generated.json"
        )
      );
      var account = CTraderAccountOptions.FromEnvironment();
      var manifestRuntime = ManifestRuntimeFactory.Create(manifest, account);

      var seeded = SeedLegacyManifestEnvironment(
        Path.Combine(
          root,
          "contracts",
          "configuration",
          "runtime-manifest-env-migration.generated.json"
        ),
        manifestRuntime,
        snapshot
      );
      Assert.True(seeded >= 100, $"expected broad migration coverage; seeded={seeded}");

      var legacyFeed = FeedOptions.FromEnvironment();
      var legacyTrade = AutoTradeOptions.FromEnvironment();
      var report = RuntimeManifestParity.Compare(
        legacyFeed,
        legacyTrade,
        manifestRuntime.Feed,
        manifestRuntime.AutoTrade
      );

      Assert.True(
        report.Mismatches.Count == 0,
        "legacy ENV and manifest-authoritative XAU differ:\n"
        + string.Join(
          "\n",
          report.Mismatches.Select(item =>
            $"{item.PropertyPath}: env={item.EnvironmentValue} manifest={item.ManifestValue}"
          )
        )
      );
      Assert.Contains(
        "XAU",
        manifestRuntime.Instruments.LiveInstruments()
          .Select(item => item.InstrumentId)
      );
    }
    finally
    {
      RestoreEnvironment(snapshot);
    }
  }

  private static int SeedLegacyManifestEnvironment(
    string migrationPath,
    ManifestRuntimeConfiguration runtime,
    Dictionary<string, string?> snapshot
  )
  {
    using var document = JsonDocument.Parse(File.ReadAllText(migrationPath));
    var seeded = 0;
    foreach (var entry in document.RootElement.GetProperty("entries").EnumerateArray())
    {
      if (
        entry.GetProperty("classification").GetString() != "manifest"
        || !entry.TryGetProperty("environment", out var environmentElement)
        || environmentElement.ValueKind != JsonValueKind.String
      )
      {
        continue;
      }
      var environment = environmentElement.GetString();
      var optionsType = entry.GetProperty("options_type").GetString();
      var propertyName = entry.GetProperty("property").GetString();
      if (string.IsNullOrWhiteSpace(environment) || string.IsNullOrWhiteSpace(propertyName))
      {
        continue;
      }

      object source = optionsType switch
      {
        "FeedOptions" => runtime.Feed,
        "AutoTradeOptions" => runtime.AutoTrade,
        _ => throw new InvalidOperationException(
          $"unknown migration options_type '{optionsType}'"
        ),
      };
      var property = source.GetType().GetProperty(
        propertyName,
        BindingFlags.Instance | BindingFlags.Public
      ) ?? throw new InvalidOperationException(
        $"migration property {optionsType}.{propertyName} does not exist"
      );
      SetEnvironment(
        snapshot,
        environment,
        ToEnvironmentString(property.GetValue(source))
      );
      seeded++;
    }
    return seeded;
  }

  private static string? ToEnvironmentString(object? value)
  {
    if (value is null)
    {
      return null;
    }
    if (value is string text)
    {
      return text;
    }
    if (value is bool flag)
    {
      return flag ? "true" : "false";
    }
    if (value is IEnumerable enumerable and not string)
    {
      var parts = new List<string>();
      foreach (var item in enumerable)
      {
        parts.Add(ToEnvironmentString(item) ?? "");
      }
      return string.Join(",", parts);
    }
    if (value is IFormattable formattable)
    {
      return formattable.ToString(null, CultureInfo.InvariantCulture);
    }
    return value.ToString();
  }

  private static void SeedAccountBootstrap(Dictionary<string, string?> snapshot)
  {
    var values = new Dictionary<string, string>(StringComparer.Ordinal)
    {
      ["CTRADER_CLIENT_ID"] = "client",
      ["CTRADER_CLIENT_SECRET"] = "secret",
      ["CTRADER_ACCESS_TOKEN"] = "access",
      ["CTRADER_REFRESH_TOKEN"] = "refresh",
      ["CTRADER_ACCOUNT_ID"] = "42",
      ["CTRADER_HOST"] = "demo.ctraderapi.com",
      ["CTRADER_PORT"] = "5035",
      ["REDIS_URL"] = "redis://localhost:6379/0",
      ["HEALTH_FILE"] = "/tmp/ctrader-feed.heartbeat",
      ["CTRADER_REFRESH_TOKEN_KEY"] = "ctrader:refresh_token",
      ["CTRADER_REFRESH_TOKEN_FILE"] = "/tmp/ctrader-token.json",
      ["CTRADER_REQUEST_TIMEOUT"] = "30",
      ["CTRADER_TOKEN_REFRESH_LEAD_DAYS"] = "5",
      ["CTRADER_TOKEN_CHECK_INTERVAL_HOURS"] = "6",
    };
    foreach (var (key, value) in values)
    {
      SetEnvironment(snapshot, key, value);
    }
  }

  private static void ClearConfigurationEnvironment(
    Dictionary<string, string?> snapshot
  )
  {
    foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
    {
      var key = entry.Key?.ToString();
      if (key is null || !IsConfigurationEnvironment(key))
      {
        continue;
      }
      SetEnvironment(snapshot, key, null);
    }
  }

  private static bool IsConfigurationEnvironment(string key) =>
    key.StartsWith("AUTO_TRADE_", StringComparison.Ordinal)
    || key.StartsWith("CTRADER_", StringComparison.Ordinal)
    || key.StartsWith("BARS_", StringComparison.Ordinal)
    || key.StartsWith("BAR_", StringComparison.Ordinal)
    || key is "REDIS_URL" or "HEALTH_FILE";

  private static void SetEnvironment(
    Dictionary<string, string?> snapshot,
    string key,
    string? value
  )
  {
    if (!snapshot.ContainsKey(key))
    {
      snapshot[key] = Environment.GetEnvironmentVariable(key);
    }
    Environment.SetEnvironmentVariable(key, value);
  }

  private static void RestoreEnvironment(Dictionary<string, string?> snapshot)
  {
    foreach (var (key, value) in snapshot)
    {
      Environment.SetEnvironmentVariable(key, value);
    }
  }

  private static string RepositoryRoot() => Path.GetFullPath(
    Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")
  );
}
