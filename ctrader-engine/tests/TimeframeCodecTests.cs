using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class TimeframeCodecTests
{
  [Fact]
  public void SupportsOneMinuteTrendbars()
  {
    Assert.Equal(60, TimeframeCodec.ToSeconds("M1"));
    Assert.Equal(ProtoOATrendbarPeriod.M1, TimeframeCodec.ToProto("M1"));
    Assert.Equal("M1", TimeframeCodec.FromProto(ProtoOATrendbarPeriod.M1));
  }

  [Fact]
  public void SupportsOneHourTrendbarsRoundTrip()
  {
    Assert.Equal(3600, TimeframeCodec.ToSeconds("H1"));
    Assert.Equal(ProtoOATrendbarPeriod.H1, TimeframeCodec.ToProto("H1"));
    Assert.Equal("H1", TimeframeCodec.FromProto(ProtoOATrendbarPeriod.H1));
    // Lowercase input must round-trip the same as the C# feed's env-driven
    // CTRADER_TIMEFRAMES list is upper-cased by ParseList, but ToSeconds/
    // ToProto themselves must not assume upper case.
    Assert.Equal(3600, TimeframeCodec.ToSeconds("h1"));
    Assert.Equal(ProtoOATrendbarPeriod.H1, TimeframeCodec.ToProto("h1"));
  }

  [Fact]
  public void DefaultTimeframeListSubscribesH1NotM30()
  {
    var parsed = TimeframeCodec.ParseList("M1,M5,M15,H1");
    Assert.Equal(["M1", "M5", "M15", "H1"], parsed);
    Assert.DoesNotContain("M30", parsed);
  }
}
