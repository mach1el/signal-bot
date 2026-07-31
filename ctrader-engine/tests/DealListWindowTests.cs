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
    var (from, to) = CTraderOpenApiFeedClient.BuildDealListWindow(openedAt, closeAt);
    Assert.True(from >= 0);
    Assert.True(to > from);
    Assert.True(to - from <= CTraderOpenApiFeedClient.MaxDealListWindowMs);
    Assert.True(to <= CTraderOpenApiFeedClient.MaxUnixMs);
  }

  [Fact]
  public void BuildDealListWindowAcceptsMillisecondInputs()
  {
    var openedAtMs = 1_700_000_000_000L;
    var closeAtMs = openedAtMs + (long)TimeSpan.FromHours(2).TotalMilliseconds;
    var (from, to) = CTraderOpenApiFeedClient.BuildDealListWindow(openedAtMs, closeAtMs);
    Assert.Equal(openedAtMs, from);
    Assert.True(to > closeAtMs);
    Assert.True(to - from <= CTraderOpenApiFeedClient.MaxDealListWindowMs);
  }

  [Fact]
  public void ToUnixMillisecondsDetectsSecondsVsMilliseconds()
  {
    Assert.Equal(1_700_000_000_000L, CTraderOpenApiFeedClient.ToUnixMilliseconds(1_700_000_000L));
    Assert.Equal(1_700_000_000_000L, CTraderOpenApiFeedClient.ToUnixMilliseconds(1_700_000_000_000L));
    Assert.Equal(0L, CTraderOpenApiFeedClient.ToUnixMilliseconds(0));
  }
}
