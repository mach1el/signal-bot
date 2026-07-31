using System.Text;
using ApexVoid.CTraderFeed;
using Xunit;

namespace CTraderFeed.Tests;

public sealed class DailyFileLogTests
{
  [Fact]
  public void DailyRotatingTextWriter_WritesToActiveFile()
  {
    var dir = Path.Combine(Path.GetTempPath(), "apexvoid-log-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(dir);
    var path = Path.Combine(dir, "ctrader-engine.log");
    try
    {
      using var consoleOut = new StringWriter();
      using var consoleErr = new StringWriter();
      using var writer = new DailyRotatingTextWriter(path, 3, consoleOut, consoleErr);
      writer.WriteLine("hello-engine-log");
      writer.Flush();
      Assert.Contains("hello-engine-log", File.ReadAllText(path));
      Assert.Contains("hello-engine-log", consoleOut.ToString());
    }
    finally
    {
      try { Directory.Delete(dir, recursive: true); } catch { /* best effort */ }
    }
  }
}
