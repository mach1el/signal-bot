using ApexVoid.CTraderFeed;
using StackExchange.Redis;

namespace CTraderFeed.Tests;

public sealed class CandidateExecutionStoreTests
{
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

  [Fact]
  public async Task TryClaimRequiresPublishedRecordWithMatchingStreamEvent()
  {
    var redisUrl = RequireRedisUrl();
    await using var store = await StackExchangeRedisSeriesCommands.ConnectAsync(
      redisUrl.Split(',', 2)[0]
    );
    var db = await ConnectDatabaseAsync(redisUrl);
    const string candidateId = "lease-integration-candidate";
    const string streamEventId = "42-0";
    var key = $"auto_trade:candidate:{candidateId}";
    await db.KeyDeleteAsync(key);
    await db.StringSetAsync(
      key,
      CandidateExecutionRecord.Published(
        candidateId,
        streamEventId,
        DateTimeOffset.UtcNow.ToUnixTimeSeconds()
      ).Serialize()
    );

    var lease = await store.TryClaimCandidateAsync(
      candidateId,
      streamEventId,
      TimeSpan.FromMinutes(2),
      CancellationToken.None
    );

    Assert.NotNull(lease);
    Assert.Equal(streamEventId, lease!.StreamEventId);
    Assert.True(
      await store.RenewCandidateLeaseAsync(
        candidateId,
        streamEventId,
        lease.Token,
        TimeSpan.FromMinutes(2),
        CancellationToken.None
      )
    );
    await store.CompleteCandidateAsync(
      candidateId,
      streamEventId,
      lease.Token,
      "ordered:123",
      CancellationToken.None
    );
    Assert.Equal(
      "ordered:123",
      await store.GetCandidateStatusAsync(candidateId, CancellationToken.None)
    );
    await db.KeyDeleteAsync(key);
  }

  [Fact]
  public async Task ReleaseRequiresMatchingLeaseToken()
  {
    var redisUrl = RequireRedisUrl();
    await using var store = await StackExchangeRedisSeriesCommands.ConnectAsync(
      redisUrl.Split(',', 2)[0]
    );
    var db = await ConnectDatabaseAsync(redisUrl);
    const string candidateId = "lease-release-candidate";
    const string streamEventId = "43-0";
    var key = $"auto_trade:candidate:{candidateId}";
    await db.KeyDeleteAsync(key);
    await db.StringSetAsync(
      key,
      CandidateExecutionRecord.Published(
        candidateId,
        streamEventId,
        DateTimeOffset.UtcNow.ToUnixTimeSeconds()
      ).Serialize()
    );

    var lease = await store.TryClaimCandidateAsync(
      candidateId,
      streamEventId,
      TimeSpan.FromMinutes(2),
      CancellationToken.None
    );
    Assert.NotNull(lease);
    Assert.False(
      await store.ReleaseCandidateAsync(
        candidateId,
        streamEventId,
        "wrong-token",
        CancellationToken.None
      )
    );
    Assert.True(
      await store.ReleaseCandidateAsync(
        candidateId,
        streamEventId,
        lease!.Token,
        CancellationToken.None
      )
    );
    Assert.Equal(
      CandidateExecutionStates.RetryableError,
      CandidateExecutionRecordParser.Parse(
        (await db.StringGetAsync(key)).ToString()
      ).State
    );
    await db.KeyDeleteAsync(key);
  }
}
