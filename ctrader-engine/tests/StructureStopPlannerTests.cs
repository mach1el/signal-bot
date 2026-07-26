using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class StructureStopPlannerTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  public static TheoryData<
    TradeDirection,
    decimal,
    decimal,
    decimal?,
    int,
    int,
    decimal,
    decimal,
    decimal,
    bool
  > PythonParityCases => new()
  {
    { TradeDirection.Buy, 4100m, 4097m, null, 20, 60, 4096.70m, 33m, 4096.7m, false },
    { TradeDirection.Sell, 4100m, 4103m, null, 20, 60, 4103.30m, 33m, 4103.3m, false },
    { TradeDirection.Buy, 4100m, 4097m, 4096m, 20, 60, 4095.85m, 41.5m, 4095.85m, false },
    { TradeDirection.Sell, 4100m, 4103m, 4104m, 20, 60, 4104.15m, 41.5m, 4104.15m, false },
    { TradeDirection.Buy, 4100m, 4099.9m, null, 20, 60, 4098m, 20m, 4099.6m, true },
    { TradeDirection.Buy, 4100m, 4090m, null, 20, 60, 4094m, 60m, 4089.7m, true },
    { TradeDirection.Buy, 4100m, 4100.29m, null, 20, 60, 4098m, 20m, 4099.99m, true },
  };

  [Theory]
  [MemberData(nameof(PythonParityCases))]
  public void MatchesPythonDecimalParityTable(
    TradeDirection direction,
    decimal entry,
    decimal swing,
    decimal? sweep,
    int minimum,
    int maximum,
    decimal expectedStop,
    decimal expectedPips,
    decimal expectedRaw,
    bool expectedClamped
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      entry,
      swing,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: sweep,
      wickBufferAtr: 0.15m,
      minimumStopPips: minimum,
      maximumStopPips: maximum,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(expectedStop, plan.StopLoss);
    Assert.Equal(expectedPips, plan.StopPips);
    Assert.Equal(expectedRaw, plan.RawStopLoss);
    Assert.Equal(expectedClamped, plan.Clamped);
    Assert.Equal(sweep is null ? "structure" : "wick", plan.Source);
  }

  [Fact]
  public void RoundingParityRecalculatesDistanceAndClampedFlag()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4100.005m,
      structureSwing: 4097.302m,
      atr: 1m,
      bufferAtr: 0m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 20,
      maximumStopPips: 60,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(4097.30m, plan.StopLoss);
    Assert.Equal(27.05m, plan.StopPips);
    Assert.True(plan.Clamped);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3998.5, 3998.2)]
  [InlineData(TradeDirection.Sell, 4001.5, 4001.8)]
  public void UsesSwingInvalidationAndAtrBuffer(
    TradeDirection direction,
    double swing,
    double expectedStop
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      Convert.ToDecimal(swing),
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 15,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal("structure", plan.Source);
    Assert.Equal(18m, plan.StopPips);
    Assert.False(plan.Clamped);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3999.5, 3998.5, 15)]
  [InlineData(TradeDirection.Sell, 4000.5, 4001.5, 15)]
  [InlineData(TradeDirection.Buy, 3990.0, 3993.5, 65)]
  [InlineData(TradeDirection.Sell, 4010.0, 4006.5, 65)]
  public void ClampsToConfiguredStopBand(
    TradeDirection direction,
    double swing,
    double expectedStop,
    int expectedPips
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      Convert.ToDecimal(swing),
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 15,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal(expectedPips, plan.StopPips);
    Assert.True(plan.Clamped);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3996.0, 3995.85)]
  [InlineData(TradeDirection.Sell, 4004.0, 4004.15)]
  public void UsesWiderSweepWickFloor(
    TradeDirection direction,
    double sweep,
    double expectedStop
  )
  {
    var plan = StructureStopPlanner.Plan(
      direction,
      4000m,
      direction == TradeDirection.Buy ? 3998.5m : 4001.5m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: Convert.ToDecimal(sweep),
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(Convert.ToDecimal(expectedStop), plan.StopLoss);
    Assert.Equal("wick", plan.Source);
  }

  [Fact]
  public void RaisesTwelveRawPipsToThirtyPipFloor()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4000m,
      structureSwing: 3999.1m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.Equal(30m, plan.StopPips);
    Assert.Equal(3997m, plan.StopLoss);
  }

  [Fact]
  public void IncidentSweepClearsWickByConfiguredAtrBuffer()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4118.8m,
      structureSwing: 4118.0m,
      atr: 2m,
      bufferAtr: 0.3m,
      sweepExtreme: 4117.5m,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol
    );

    Assert.True(plan.StopLoss <= 4117.2m);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 3993.6)]
  [InlineData(TradeDirection.Sell, 4006.4)]
  public void RejectsWhenSweepWickFloorExceedsEnvelope(
    TradeDirection direction,
    double sweep
  )
  {
    var error = Assert.Throws<VolumePlanningException>(() =>
      StructureStopPlanner.Plan(
        direction,
        4000m,
        direction == TradeDirection.Buy ? 3998.5m : 4001.5m,
        atr: 1m,
        bufferAtr: 0.3m,
        sweepExtreme: Convert.ToDecimal(sweep),
        wickBufferAtr: 0.15m,
        minimumStopPips: 30,
        maximumStopPips: 65,
        pipSize: 0.1m,
        Symbol
      )
    );

    Assert.Equal("stop_exceeds_envelope_after_wick", error.Message);
  }

  [Fact]
  public void PushesStopBeyondExecutionGradeOpposingZone()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      entryPrice: 4000.2m,
      structureSwing: 3998m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol,
      new OpposingZoneStopContext(
        "demand-1",
        3997m,
        3998.5m,
        ExecutionGrade: true,
        PushBeyondZone: true,
        BufferAtr: 0.3m
      )
    );

    Assert.Equal(3997.20m, plan.BaseStopLoss);
    Assert.Equal(3996.70m, plan.StopLoss);
    Assert.Equal("opposing_zone_push", plan.Adjustment);
    Assert.Equal(2, plan.Version);
  }

  [Fact]
  public void RejectsStopInsideOpposingZoneWhenPushDisabled()
  {
    var error = Assert.Throws<VolumePlanningException>(() =>
      StructureStopPlanner.Plan(
        TradeDirection.Buy,
        4000m,
        3998m,
        atr: 1m,
        bufferAtr: 0.3m,
        sweepExtreme: null,
        wickBufferAtr: 0.15m,
        minimumStopPips: 30,
        maximumStopPips: 65,
        pipSize: 0.1m,
        Symbol,
        new OpposingZoneStopContext(
          "demand-1",
          3997m,
          3998.5m,
          ExecutionGrade: true,
          PushBeyondZone: false,
          BufferAtr: 0.3m
        )
      )
    );

    Assert.Equal("stop_inside_opposing_zone", error.Message);
  }

  [Fact]
  public void ContextOnlyOpposingZoneLeavesBaseStopUntouched()
  {
    var plan = StructureStopPlanner.Plan(
      TradeDirection.Buy,
      4000m,
      3998m,
      atr: 1m,
      bufferAtr: 0.3m,
      sweepExtreme: null,
      wickBufferAtr: 0.15m,
      minimumStopPips: 30,
      maximumStopPips: 65,
      pipSize: 0.1m,
      Symbol,
      new OpposingZoneStopContext(
        "demand-wide",
        3990m,
        4025m,
        ExecutionGrade: false,
        PushBeyondZone: true,
        BufferAtr: 0.3m
      )
    );

    Assert.Equal(plan.BaseStopLoss, plan.StopLoss);
    Assert.Equal("none", plan.Adjustment);
  }
}
