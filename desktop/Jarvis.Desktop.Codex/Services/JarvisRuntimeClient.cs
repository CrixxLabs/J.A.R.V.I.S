using System.Net.Http;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using Jarvis.Desktop.Models;

namespace Jarvis.Desktop.Services;

/// <summary>Non-blocking, reconnecting client for the localhost Python SSE bridge.</summary>
public sealed class JarvisRuntimeClient : IAsyncDisposable
{
    private readonly HttpClient _http = new() { Timeout = Timeout.InfiniteTimeSpan };
    private readonly Uri _eventsUri;
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) }
    };
    private CancellationTokenSource? _stop;
    private Task? _worker;
    private bool _connected;

    public JarvisRuntimeClient(string? baseAddress = null)
    {
        var configured = baseAddress ?? Environment.GetEnvironmentVariable("JARVIS_VISUAL_URL") ?? "http://127.0.0.1:8765";
        _eventsUri = new Uri(new Uri(configured.TrimEnd('/') + "/"), "v1/events");
    }

    public event EventHandler<JarvisVisualSignals>? SnapshotReceived;
    public event EventHandler<bool>? ConnectionChanged;

    public void Start()
    {
        if (_worker is { IsCompleted: false }) return;
        _stop = new CancellationTokenSource();
        _worker = RunAsync(_stop.Token);
    }

    private async Task RunAsync(CancellationToken cancellationToken)
    {
        var retry = TimeSpan.FromMilliseconds(250);
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, _eventsUri);
                using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
                response.EnsureSuccessStatusCode();
                SetConnected(true);
                retry = TimeSpan.FromMilliseconds(250);
                await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
                using var reader = new StreamReader(stream);
                while (!cancellationToken.IsCancellationRequested)
                {
                    var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
                    if (line is null) throw new IOException("Runtime event stream closed.");
                    if (!line.StartsWith("data: ", StringComparison.Ordinal)) continue;
                    var snapshot = JsonSerializer.Deserialize<JarvisVisualSignals>(line.AsSpan(6), _json);
                    if (snapshot is not null) SnapshotReceived?.Invoke(this, snapshot);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
            catch (Exception)
            {
                SetConnected(false);
                try { await Task.Delay(retry, cancellationToken).ConfigureAwait(false); }
                catch (OperationCanceledException) { }
                retry = TimeSpan.FromMilliseconds(Math.Min(retry.TotalMilliseconds * 1.8, 5000));
            }
        }
        SetConnected(false);
    }

    private void SetConnected(bool connected)
    {
        if (_connected == connected) return;
        _connected = connected;
        ConnectionChanged?.Invoke(this, connected);
    }

    public async ValueTask DisposeAsync()
    {
        if (_stop is null) return;
        _stop.Cancel();
        if (_worker is not null)
        {
            try { await _worker.ConfigureAwait(false); }
            catch (OperationCanceledException) { }
        }
        _stop.Dispose();
        _http.Dispose();
    }
}
