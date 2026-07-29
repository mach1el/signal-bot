using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AccessTokenRequestGateTests
{
  [Fact]
  public async Task WaitingAuthenticatedRequestReadsTokenAfterRotationCompletes()
  {
    var accessToken = "old-access";
    using var requestLock = new SemaphoreSlim(1, 1);
    var gate = new AccessTokenRequestGate(requestLock, () => accessToken);
    var refreshEntered = new TaskCompletionSource<bool>(
      TaskCreationOptions.RunContinuationsAsynchronously
    );
    var finishRefresh = new TaskCompletionSource<bool>(
      TaskCreationOptions.RunContinuationsAsynchronously
    );

    await requestLock.WaitAsync();
    var refresh = Task.Run(
      async () =>
      {
        refreshEntered.TrySetResult(true);
        await finishRefresh.Task;
        accessToken = "rotated-access";
        requestLock.Release();
      }
    );
    await refreshEntered.Task;

    string? requestToken = null;
    var accountList = gate.RunAsync(
      token =>
      {
        requestToken = token;
        return Task.FromResult(true);
      },
      CancellationToken.None
    );

    Assert.False(accountList.IsCompleted);
    finishRefresh.TrySetResult(true);
    await Task.WhenAll(refresh, accountList);
    Assert.Equal("rotated-access", requestToken);
  }
}
