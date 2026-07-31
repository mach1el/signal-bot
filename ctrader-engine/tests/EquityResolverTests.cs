using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class EquityResolverTests
{
  private static TradingAccountSnapshot Account(
    decimal balance,
    decimal equity,
    string equitySource = "",
    long snapshotTs = 1_720_000_000
  ) => new(
    1, false, "ScopeTrade", "FullAccess", "Hedged", "Fusion",
    balance, equity, snapshotTs, equitySource
  );

  [Fact]
  public void PrefersRealEquityWhenPresent()
  {
    var resolved = EquityResolver.Resolve(
      Account(1_000m, 1_300m, "test"),
      openPositionCount: 2,
      pendingOrderCount: 1
    );

    Assert.Equal(1_300m, resolved.Equity);
    Assert.Equal(EquityResolver.SourceAccountEquity, resolved.EquitySource);
    Assert.Equal(1_000m, resolved.AccountBalance);
    Assert.Equal(1_300m, resolved.AccountEquity);
    Assert.Equal(2, resolved.OpenPositionCount);
    Assert.Equal(1, resolved.PendingOrderCount);
  }

  [Fact]
  public void BalanceProxyFallsBackOnlyWhenFlat()
  {
    var resolved = EquityResolver.Resolve(
      Account(1_300m, 1_300m, EquityResolver.SourceBalanceProxy),
      openPositionCount: 0,
      pendingOrderCount: 0
    );

    Assert.Equal(1_300m, resolved.Equity);
    Assert.Equal(EquityResolver.SourceBalanceFlatFallback, resolved.EquitySource);
    Assert.Contains(
      "equity_source=balance_flat_account_fallback",
      EquityResolver.FormatTelemetry(resolved)
    );
  }

  [Fact]
  public void BalanceProxyWithOpenExposureUsesBalanceNotReject()
  {
    // Production ProtoOATrader has no Equity field — rejecting every plan
    // while any position/order exists was the equity_unavailable_with_open_exposure
    // deadlock (Key Level plan published then immediately rejected).
    var resolved = EquityResolver.Resolve(
      Account(1_300m, 1_300m, EquityResolver.SourceBalanceProxy),
      openPositionCount: 1,
      pendingOrderCount: 0
    );

    Assert.Equal(1_300m, resolved.Equity);
    Assert.Equal(
      EquityResolver.SourceBalanceProxyWithExposure,
      resolved.EquitySource
    );
  }

  [Fact]
  public void BalancePlusUnrealizedWhenPositionsExposeNetProfit()
  {
    var positions = new[]
    {
      new TradingPosition(
        1, 7, TradeDirection.Sell, 800, 4098m, null,
        "apexvoid-auto", "v7|p|t|L1", NetProfit: -20m
      ),
      new TradingPosition(
        2, 7, TradeDirection.Sell, 300, 4100m, null,
        "apexvoid-auto", "v7|p|t|L2", NetProfit: 5m
      ),
    };

    var resolved = EquityResolver.Resolve(
      Account(1_300m, 1_300m, EquityResolver.SourceBalanceProxy),
      openPositionCount: 2,
      pendingOrderCount: 0,
      positions
    );

    Assert.Equal(1_285m, resolved.Equity);
    Assert.Equal(EquityResolver.SourceBalancePlusUnrealized, resolved.EquitySource);
  }

  [Fact]
  public void ZeroEquityWithPendingOrdersThrows()
  {
    var error = Assert.Throws<TradePlanContractException>(() =>
      EquityResolver.Resolve(
        Account(0m, 0m),
        openPositionCount: 0,
        pendingOrderCount: 1
      )
    );
    Assert.Equal(EquityResolver.UnavailableWithOpenExposure, error.Message);
  }
}
