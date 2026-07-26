using ApexVoid.CTraderFeed;
using StackExchange.Redis;

namespace CTraderFeed.Tests;

// Real Redis 7 coverage for the fenced candidate-execution state machine.
// Requires REAL_REDIS_URL; the tests fail rather than skip so the matrix can
// never silently disappear from a validation run.
public sealed class CandidateExecutionStoreTests
{
  private static readonly TimeSpan Lease = TimeSpan.FromMinutes(2);

  private static string RequireRedisUrl() =>
    Environment.GetEnvironmentVariable("REAL_REDIS_URL")
    ?? throw new InvalidOperationException(
      "REAL_REDIS_URL is required for candidate lease integration tests"
    );

  private static async Task<IDatabase> ConnectDatabaseAsync(string redisUrl)
  {
    // Prefer StackExchange configuration form when callers pass host:port.
    // URI form redis://host:port/db is also accepted by ConnectAsync below.
    var options = ConfigurationOptions.Parse(
      redisUrl.StartsWith("redis", StringComparison.OrdinalIgnoreCase)
        ? NormalizeRedisUri(redisUrl)
        : redisUrl
    );
    options.AbortOnConnectFail = false;
    var mux = await ConnectionMultiplexer.ConnectAsync(options);
    return mux.GetDatabase();
  }

  private static string NormalizeRedisUri(string redisUrl)
  {
    var uri = new Uri(redisUrl.Split(',', 2)[0]);
    var dbText = uri.AbsolutePath.Trim('/');
    var db = int.TryParse(dbText, out var database) ? database : 0;
    return $"{uri.Host}:{uri.Port},defaultDatabase={db},abortConnect=false";
  }

  private sealed record Fixture(
    StackExchangeRedisSeriesCommands Store,
    IDatabase Db,
    string CandidateId,
    string StreamEventId,
    string Key
  ) : IAsyncDisposable
  {
    public async ValueTask DisposeAsync()
    {
      await Db.KeyDeleteAsync(Key);
      await Store.DisposeAsync();
    }

    public async Task SeedRawAsync(string raw, TimeSpan? ttl = null)
    {
      await Db.StringSetAsync(Key, raw);
      if (ttl is TimeSpan window)
      {
        await Db.KeyExpireAsync(Key, window);
      }
    }

    public async Task SeedPublishedAsync() => await SeedRawAsync(
      CandidateExecutionRecord.Published(
        CandidateId,
        StreamEventId,
        DateTimeOffset.UtcNow.ToUnixTimeSeconds()
      ).Serialize()
    );

    public async Task<CandidateExecutionRecord> ReadAsync() =>
      CandidateExecutionRecordParser.Parse(
        (await Db.StringGetAsync(Key)).ToString()
      );

    // Rewrites the current record with a lease that already expired so a
    // successor can reclaim without waiting out a real lease window.
    public async Task ExpireLeaseAsync()
    {
      var record = await ReadAsync();
      await SeedRawAsync((record with
      {
        LeaseExpiresAt = DateTimeOffset.UtcNow.AddMinutes(-5).ToUnixTimeSeconds(),
      }).Serialize());
    }
  }

  private static async Task<Fixture> StartAsync(string name)
  {
    var redisUrl = RequireRedisUrl();
    var store = await StackExchangeRedisSeriesCommands.ConnectAsync(
      redisUrl.Split(',', 2)[0]
    );
    var db = await ConnectDatabaseAsync(redisUrl);
    var candidateId = $"lease-{name}";
    var key = $"auto_trade:candidate:{candidateId}";
    await db.KeyDeleteAsync(key);
    return new Fixture(store, db, candidateId, "42-0", key);
  }

  // ---------------------------------------------------------------- acquire

  [Fact]
  public async Task PublishedRecordIsClaimedIntoProcessing()
  {
    await using var fixture = await StartAsync("published-claim");
    await fixture.SeedPublishedAsync();

    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.Claimed, claim.Disposition);
    Assert.NotNull(claim.Lease);
    Assert.Equal(fixture.StreamEventId, claim.Lease!.StreamEventId);
    var record = await fixture.ReadAsync();
    Assert.Equal(CandidateExecutionStates.Processing, record.State);
    Assert.Equal(claim.Lease.Token, record.LeaseToken);
    Assert.Equal(1, record.Attempt);
  }

  [Fact]
  public async Task RetryableErrorRecordIsReclaimedWithFreshToken()
  {
    await using var fixture = await StartAsync("retry-reclaim");
    await fixture.SeedPublishedAsync();
    var first = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    Assert.True(
      await fixture.Store.ReleaseCandidateAsync(
        fixture.CandidateId,
        fixture.StreamEventId,
        first.Lease!.Token,
        CancellationToken.None,
        "redis_timeout"
      )
    );
    Assert.Equal(
      CandidateExecutionStates.RetryableError,
      (await fixture.ReadAsync()).State
    );

    var retry = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.Claimed, retry.Disposition);
    Assert.NotEqual(first.Lease.Token, retry.Lease!.Token);
    var record = await fixture.ReadAsync();
    Assert.Equal(fixture.CandidateId, record.CandidateId);
    Assert.Equal(fixture.StreamEventId, record.StreamEventId);
    Assert.Equal(2, record.Attempt);
  }

  [Fact]
  public async Task UnexpiredProcessingLeaseIsActiveElsewhere()
  {
    await using var fixture = await StartAsync("active-denied");
    await fixture.SeedPublishedAsync();
    await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    var second = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.ActiveElsewhere, second.Disposition);
    Assert.False(second.AdvancesCursor);
  }

  [Fact]
  public async Task ExpiredProcessingLeaseIsReclaimed()
  {
    await using var fixture = await StartAsync("expired-reclaim");
    await fixture.SeedPublishedAsync();
    var first = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    await fixture.ExpireLeaseAsync();

    var second = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.Claimed, second.Disposition);
    Assert.NotEqual(first.Lease!.Token, second.Lease!.Token);
  }

  [Fact]
  public async Task ExpiredBrokerSubmittingIsReclaimedOnlyUnderSafePolicy()
  {
    await using var fixture = await StartAsync("submitting-reclaim");
    await fixture.SeedPublishedAsync();
    var first = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    Assert.True(
      await fixture.Store.TransitionCandidateStateAsync(
        fixture.CandidateId,
        fixture.StreamEventId,
        first.Lease!.Token,
        CandidateExecutionStates.BrokerSubmitting,
        CancellationToken.None
      )
    );
    await fixture.ExpireLeaseAsync();

    var denied = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    Assert.Equal(CandidateClaimDisposition.RecoveryRequired, denied.Disposition);

    var allowed = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None,
      CandidateClaimPolicy.Recovery
    );
    Assert.Equal(CandidateClaimDisposition.Claimed, allowed.Disposition);
  }

  [Theory]
  [InlineData("ordered:9001")]
  [InlineData("rejected:stale candidate")]
  [InlineData("dry_run")]
  [InlineData("flip_pending:BUY:9999999999")]
  public async Task TerminalRecordIsDeniedAndAdvancesCursor(string legacyOutcome)
  {
    await using var fixture = await StartAsync("terminal-denied");
    await fixture.SeedRawAsync(legacyOutcome);

    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.Terminal, claim.Disposition);
    Assert.True(claim.AdvancesCursor);
  }

  [Fact]
  public async Task BrokerOutcomeUnknownIsDeniedToNormalClaim()
  {
    await using var fixture = await StartAsync("unknown-denied");
    await fixture.SeedPublishedAsync();
    var first = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      first.Lease!.Token,
      CandidateExecutionStates.BrokerOutcomeUnknown,
      CancellationToken.None
    );

    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.RecoveryRequired, claim.Disposition);
    Assert.False(claim.AdvancesCursor);
    Assert.Equal(
      CandidateExecutionStates.BrokerOutcomeUnknown,
      (await fixture.ReadAsync()).State
    );
  }

  [Fact]
  public async Task LegacyProcessingRemainsOwnedWhileTtlHolds()
  {
    await using var fixture = await StartAsync("legacy-processing");
    await fixture.SeedRawAsync("processing", TimeSpan.FromMinutes(2));

    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.ActiveElsewhere, claim.Disposition);
  }

  [Fact]
  public async Task UnknownLegacyStringIsAConflictNotARetry()
  {
    await using var fixture = await StartAsync("unknown-legacy");
    await fixture.SeedRawAsync("something_we_never_wrote");

    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.Equal(CandidateClaimDisposition.Conflict, claim.Disposition);
    Assert.False(claim.AdvancesCursor);
  }

  // ---------------------------------------------------------------- fencing

  [Fact]
  public async Task WrongTokenIsDeniedForEveryLeaseOwnedMutation()
  {
    await using var fixture = await StartAsync("wrong-token");
    await fixture.SeedPublishedAsync();
    await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );

    Assert.False(await fixture.Store.RenewCandidateLeaseAsync(
      fixture.CandidateId, fixture.StreamEventId, "nope", Lease, CancellationToken.None
    ));
    Assert.False(await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      "nope",
      CandidateExecutionStates.BrokerSubmitting,
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, "nope", "ordered:1", CancellationToken.None
    ));
    Assert.False(await fixture.Store.ReleaseCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, "nope", CancellationToken.None
    ));
    Assert.Equal(
      CandidateExecutionStates.Processing,
      (await fixture.ReadAsync()).State
    );
  }

  [Fact]
  public async Task MissingCurrentTokenNeverAuthorisesCompletion()
  {
    await using var fixture = await StartAsync("missing-token");
    // A record in a lease-owned state but with no lease token must not be
    // completable: a null token is not permission.
    await fixture.SeedRawAsync(new CandidateExecutionRecord(
      fixture.CandidateId,
      fixture.StreamEventId,
      CandidateExecutionStates.Processing,
      null,
      DateTimeOffset.UtcNow.AddMinutes(2).ToUnixTimeSeconds(),
      null,
      DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
      1
    ).Serialize());

    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      "any-token",
      "ordered:1",
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      "",
      "ordered:1",
      CancellationToken.None
    ));
    Assert.Equal(
      CandidateExecutionStates.Processing,
      (await fixture.ReadAsync()).State
    );
  }

  [Fact]
  public async Task StaleOwnerCannotMutateOrOverwriteSuccessor()
  {
    await using var fixture = await StartAsync("stale-owner");
    await fixture.SeedPublishedAsync();
    var ownerA = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    await fixture.ExpireLeaseAsync();
    var ownerB = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    Assert.Equal(CandidateClaimDisposition.Claimed, ownerB.Disposition);

    // A is stale: it can neither complete nor release B's active record.
    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      ownerA.Lease!.Token,
      "ordered:111",
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.ReleaseCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      ownerA.Lease.Token,
      CancellationToken.None
    ));

    // B finishes, and A still cannot rewrite the terminal outcome.
    Assert.True(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      ownerB.Lease!.Token,
      "ordered:222",
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      ownerA.Lease.Token,
      "ordered:111",
      CancellationToken.None
    ));
    Assert.Equal("ordered:222", (await fixture.ReadAsync()).Outcome);
  }

  [Fact]
  public async Task TerminalOutcomeIsImmutableToAnyLaterToken()
  {
    await using var fixture = await StartAsync("terminal-immutable");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    Assert.True(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      "ordered:777",
      CancellationToken.None
    ));

    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      "rejected:changed my mind",
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.ReleaseCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CandidateExecutionStates.BrokerOutcomeUnknown,
      CancellationToken.None
    ));
    Assert.Equal("ordered:777", (await fixture.ReadAsync()).Outcome);
  }

  [Fact]
  public async Task IdentityMismatchIsDenied()
  {
    await using var fixture = await StartAsync("identity-mismatch");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );

    Assert.False(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      "999-0",
      owner.Lease!.Token,
      "ordered:1",
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.CompleteCandidateAsync(
      "some-other-candidate-id",
      fixture.StreamEventId,
      owner.Lease.Token,
      "ordered:1",
      CancellationToken.None
    ));
    var conflict = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      "999-0",
      Lease,
      CancellationToken.None
    );
    Assert.Equal(CandidateClaimDisposition.Conflict, conflict.Disposition);
    Assert.Equal(
      CandidateExecutionStates.Processing,
      (await fixture.ReadAsync()).State
    );
  }

  [Fact]
  public async Task StructuredRecordMissingIdentityIsConflict()
  {
    await using var fixture = await StartAsync("missing-identity");
    await fixture.SeedRawAsync(
      """{"version":1,"candidate_id":"","stream_event_id":"1-0","state":"published","updated_at":1}"""
    );
    var missingCandidate = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    Assert.Equal(CandidateClaimDisposition.Conflict, missingCandidate.Disposition);

    await fixture.SeedRawAsync(
      """{"version":1,"candidate_id":"cand","stream_event_id":"","state":"published","updated_at":1}"""
    );
    var missingEvent = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    Assert.Equal(CandidateClaimDisposition.Conflict, missingEvent.Disposition);
  }

  // -------------------------------------------------------------- heartbeat

  [Fact]
  public async Task HeartbeatExtendsTtlAndBlocksSuccessorUntilCompletion()
  {
    await using var fixture = await StartAsync("heartbeat");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      TimeSpan.FromSeconds(4),
      CancellationToken.None
    );

    await using (
      var heartbeat = CandidateLeaseHeartbeat.Start(
        fixture.Store,
        owner.Lease!,
        TimeSpan.FromSeconds(4),
        CancellationToken.None,
        TimeSpan.FromMilliseconds(200)
      )
    )
    {
      await Task.Delay(TimeSpan.FromSeconds(1));
      Assert.False(heartbeat.OwnershipLost);
      Assert.True(heartbeat.Renewals > 0);
      var ttl = await fixture.Db.KeyTimeToLiveAsync(fixture.Key);
      Assert.NotNull(ttl);
      Assert.True(ttl!.Value > TimeSpan.FromSeconds(2));

      // A successor cannot claim while the heartbeat keeps the lease alive.
      var successor = await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId,
        fixture.StreamEventId,
        Lease,
        CancellationToken.None
      );
      Assert.Equal(
        CandidateClaimDisposition.ActiveElsewhere,
        successor.Disposition
      );

      Assert.True(await fixture.Store.CompleteCandidateAsync(
        fixture.CandidateId,
        fixture.StreamEventId,
        owner.Lease!.Token,
        "ordered:555",
        CancellationToken.None
      ));
    }

    // Once terminal, further renewal must fail rather than resurrect the lease.
    Assert.False(await fixture.Store.RenewCandidateLeaseAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      Lease,
      CancellationToken.None
    ));
    Assert.Equal("ordered:555", (await fixture.ReadAsync()).Outcome);
  }

  [Fact]
  public async Task StaleHeartbeatCannotRenewSuccessorLease()
  {
    await using var fixture = await StartAsync("stale-heartbeat");
    await fixture.SeedPublishedAsync();
    var ownerA = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    await fixture.ExpireLeaseAsync();
    var ownerB = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );

    await using var staleHeartbeat = CandidateLeaseHeartbeat.Start(
      fixture.Store,
      ownerA.Lease!,
      TimeSpan.FromSeconds(4),
      CancellationToken.None,
      TimeSpan.FromMilliseconds(100)
    );

    Assert.False(
      await staleHeartbeat.EnsureOwnershipAsync(CancellationToken.None)
    );
    Assert.True(staleHeartbeat.OwnershipLost);
    Assert.Equal(ownerB.Lease!.Token, (await fixture.ReadAsync()).LeaseToken);
  }

  [Fact]
  public async Task OwnershipTokenCancelsEvenWhenMetricWriteFails()
  {
    await using var fixture = await StartAsync("heartbeat-metric-fail");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    await fixture.ExpireLeaseAsync();
    _ = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None
    );
    using var sessionCts = new CancellationTokenSource();
    sessionCts.Cancel();
    var metricFailures = 0;
    await using var heartbeat = CandidateLeaseHeartbeat.Start(
      fixture.Store,
      owner.Lease!,
      Lease,
      sessionCts.Token,
      TimeSpan.FromHours(1),
      metric: (_, _) =>
      {
        metricFailures++;
        return Task.FromException(
          new InvalidOperationException("metric sink unavailable")
        );
      }
    );

    Assert.False(await heartbeat.EnsureOwnershipAsync(CancellationToken.None));
    Assert.True(heartbeat.OwnershipLost);
    Assert.True(heartbeat.OwnershipToken.IsCancellationRequested);
    Assert.True(metricFailures >= 1);
  }

  // ------------------------------------------------------- state transitions

  [Fact]
  public async Task InvalidTransitionsAreDenied()
  {
    await using var fixture = await StartAsync("invalid-transition");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );

    // processing cannot jump straight to a terminal state via transition, and
    // broker_submitting can never be released as a safe retry.
    Assert.False(await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      CandidateExecutionStates.Ordered,
      CancellationToken.None
    ));
    Assert.True(await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CandidateExecutionStates.BrokerSubmitting,
      CancellationToken.None
    ));
    Assert.False(await fixture.Store.ReleaseCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CancellationToken.None
    ));
    Assert.Equal(
      CandidateExecutionStates.BrokerSubmitting,
      (await fixture.ReadAsync()).State
    );
  }

  [Fact]
  public async Task BrokerUnknownIsPreservedAndAdoptedOnlyViaRecoveryFlow()
  {
    await using var fixture = await StartAsync("recovery-adoption");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      CandidateExecutionStates.BrokerSubmitting,
      CancellationToken.None
    );
    Assert.True(await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CandidateExecutionStates.BrokerOutcomeUnknown,
      CancellationToken.None,
      "broker_response_lost"
    ));

    // Normal claim refuses; recovery claim adopts only after the lease lapses.
    Assert.Equal(
      CandidateClaimDisposition.RecoveryRequired,
      (await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
      )).Disposition
    );
    Assert.Equal(
      CandidateClaimDisposition.ActiveElsewhere,
      (await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId,
        fixture.StreamEventId,
        Lease,
        CancellationToken.None,
        CandidateClaimPolicy.Recovery
      )).Disposition
    );

    await fixture.ExpireLeaseAsync();
    var recovery = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      Lease,
      CancellationToken.None,
      CandidateClaimPolicy.Recovery
    );
    Assert.Equal(CandidateClaimDisposition.Claimed, recovery.Disposition);
    Assert.True(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      recovery.Lease!.Token,
      "ordered:4242",
      CancellationToken.None
    ));
    Assert.Equal("ordered:4242", (await fixture.ReadAsync()).Outcome);
  }

  [Fact]
  public async Task ConfirmedBrokerAbsenceAllowsRetryFromRecoveryState()
  {
    await using var fixture = await StartAsync("confirmed-absent");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    await fixture.Store.TransitionCandidateStateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      CandidateExecutionStates.BrokerOutcomeUnknown,
      CancellationToken.None
    );

    Assert.True(await fixture.Store.ReleaseCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease.Token,
      CancellationToken.None,
      "broker_outcome_confirmed_absent"
    ));
    var record = await fixture.ReadAsync();
    Assert.Equal(CandidateExecutionStates.RetryableError, record.State);
    Assert.Equal("broker_outcome_confirmed_absent", record.LastError);
    Assert.Equal(
      CandidateClaimDisposition.Claimed,
      (await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
      )).Disposition
    );
  }

  // JSON-special characters in outcomes and errors must survive Lua encoding.
  [Fact]
  public async Task OutcomesWithJsonSpecialCharactersRoundTrip()
  {
    await using var fixture = await StartAsync("json-escaping");
    await fixture.SeedPublishedAsync();
    var owner = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId, fixture.StreamEventId, Lease, CancellationToken.None
    );
    const string hostileOutcome =
      "rejected:broker said \"no\" \\ {\"state\":\"ordered\"} \n done";

    Assert.True(await fixture.Store.CompleteCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      owner.Lease!.Token,
      hostileOutcome,
      CancellationToken.None
    ));

    var record = await fixture.ReadAsync();
    Assert.Equal(CandidateExecutionStates.Rejected, record.State);
    Assert.Equal(hostileOutcome, record.Outcome);
  }

  [Fact]
  public async Task FlipClaimOverrideOnlyRewritesFlipMarkers()
  {
    await using var fixture = await StartAsync("flip-override");
    await fixture.SeedRawAsync("flip_pending:BUY:9999999999");

    Assert.True(await fixture.Store.OverrideFlipClaimAsync(
      fixture.CandidateId,
      "rejected:flip_expired",
      CancellationToken.None
    ));
    Assert.Equal("rejected:flip_expired", (await fixture.ReadAsync()).Outcome);

    // A real broker outcome is never rewritable through this path.
    await fixture.SeedRawAsync("ordered:31337");
    Assert.False(await fixture.Store.OverrideFlipClaimAsync(
      fixture.CandidateId,
      "rejected:flip_expired",
      CancellationToken.None
    ));
    Assert.Equal("ordered:31337", (await fixture.ReadAsync()).Outcome);
  }

  [Fact]
  public async Task RejectedFlipClaimIsReArmableButOtherRejectionsAreTerminal()
  {
    await using var fixture = await StartAsync("flip-rearm");
    await fixture.SeedRawAsync("rejected:flip_released");

    Assert.Equal(
      CandidateClaimDisposition.Terminal,
      (await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId, "", Lease, CancellationToken.None
      )).Disposition
    );
    Assert.Equal(
      CandidateClaimDisposition.Claimed,
      (await fixture.Store.TryClaimCandidateAsync(
        fixture.CandidateId,
        "",
        Lease,
        CancellationToken.None,
        CandidateClaimPolicy.FlipClaim
      )).Disposition
    );
  }

  // Lease expiry is evaluated with Redis server time, so a skewed executor
  // clock cannot steal or prolong ownership.
  [Fact]
  public async Task LeaseExpiryUsesRedisServerTime()
  {
    await using var fixture = await StartAsync("server-time");
    await fixture.SeedPublishedAsync();
    var claim = await fixture.Store.TryClaimCandidateAsync(
      fixture.CandidateId,
      fixture.StreamEventId,
      TimeSpan.FromSeconds(90),
      CancellationToken.None
    );

    var record = await fixture.ReadAsync();
    Assert.NotNull(record.LeaseExpiresAt);
    Assert.Equal(
      record.LeaseExpiresAt!.Value,
      claim.Lease!.ExpiresAt.ToUnixTimeSeconds()
    );
    var hostNow = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    Assert.InRange(record.LeaseExpiresAt.Value - hostNow, 60, 120);
  }
}
