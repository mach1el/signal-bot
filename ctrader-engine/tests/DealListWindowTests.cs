using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class DealListWindowTests
{
  [Fact]
  public void BuildDealListWindowStaysUnderOneWeekInclusiveOfToPadding()
  {
    // Old logic used maxLookback=7d and to=close+1m → INCORRECT_BOUNDARIES.
    var openedAt = 1_700_000_000L; // seconds
    var closeAt = openedAt + (long)TimeSpan.FromDays(10).TotalSeconds;
    var nowMs = closeAt * 1000L + 60_000L;
    var (from, to) = CTraderOpenApiFeedClient.BuildDealListWindow(
      openedAt,
      closeAt,
      nowMs
    );
    Assert.True(from >= 0);
    Assert.True(to > from);
    Assert.True(to - from <= CTraderOpenApiFeedClient.MaxDealListWindowMs);
    Assert.True(to <= CTraderOpenApiFeedClient.MaxUnixMs);
    Assert.True(to <= nowMs);
  }

  [Fact]
  public void BuildDealListWindowAcceptsMillisecondInputs()
  {
    var openedAtMs = 1_700_000_000_000L;
    var closeAtMs = openedAtMs + (long)TimeSpan.FromHours(2).TotalMilliseconds;
    var nowMs = closeAtMs + 60_000L;
    var (from, to) = CTraderOpenApiFeedClient.BuildDealListWindow(
      openedAtMs,
      closeAtMs,
      nowMs
    );
    Assert.Equal(openedAtMs, from);
    Assert.True(to >= closeAtMs);
    Assert.True(to - from <= CTraderOpenApiFeedClient.MaxDealListWindowMs);
  }

  [Fact]
  public void BuildDealListWindowNeverRequestsFutureToTimestamp()
  {
    // BE/SL reconcile often passes approximateClose≈now; close+1m must not
    // produce a future ToTimestamp (cTrader → INCORRECT_BOUNDARIES).
    var openedAt = 1_750_000_000L;
    var closeAt = openedAt + 3_600L;
    var nowMs = closeAt * 1000L;
    var (from, to) = CTraderOpenApiFeedClient.BuildDealListWindow(
      openedAt,
      closeAt,
      nowMs
    );
    Assert.True(to <= nowMs);
    Assert.True(to > from);
    Assert.Equal(openedAt * 1000L, from);
  }

  [Fact]
  public void IsRetryableDealListFailureCoversTimeoutAndBoundaries()
  {
    Assert.True(
      CTraderOpenApiFeedClient.IsRetryableDealListFailure(
        new TimeoutException("Timed out after 30s waiting for ProtoOADealListByPositionIdRes")
      )
    );
    Assert.True(
      CTraderOpenApiFeedClient.IsRetryableDealListFailure(
        new InvalidOperationException("cTrader Open API error: INCORRECT_BOUNDARIES")
      )
    );
    Assert.False(
      CTraderOpenApiFeedClient.IsRetryableDealListFailure(
        new InvalidOperationException("cTrader Open API error: ALREADY_LOGGED_IN")
      )
    );
  }

  [Fact]
  public void DealListLookupTimeoutStaysWellUnderTheDefaultRequestTimeout()
  {
    // This lookup is best-effort (close reason/exit price for a position
    // the broker already closed) and shares one global in-flight slot with
    // live order management. Seen live: a windowed timeout followed by a
    // fallback timeout at the full 30s RequestTimeout held that slot -
    // blocking SL trailing / order submission for every other open
    // position - for 60s after every SL/BE close. Both legs together must
    // stay far below the 30s default so a slow/unresponsive broker fails
    // this diagnostic lookup fast instead of stalling the whole client.
    Assert.True(CTraderOpenApiFeedClient.DealListLookupTimeout <= TimeSpan.FromSeconds(3));
    Assert.True(CTraderOpenApiFeedClient.DealListLookupTimeout * 2 < TimeSpan.FromSeconds(10));
  }

  [Fact]
  public void ToUnixMillisecondsDetectsSecondsVsMilliseconds()
  {
    Assert.Equal(1_700_000_000_000L, CTraderOpenApiFeedClient.ToUnixMilliseconds(1_700_000_000L));
    Assert.Equal(1_700_000_000_000L, CTraderOpenApiFeedClient.ToUnixMilliseconds(1_700_000_000_000L));
    Assert.Equal(0L, CTraderOpenApiFeedClient.ToUnixMilliseconds(0));
  }
}
