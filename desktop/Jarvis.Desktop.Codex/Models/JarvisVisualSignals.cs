namespace Jarvis.Desktop.Models;

/// <summary>
/// Backend-facing visual contract. Null amplitudes select the built-in prototype simulation;
/// normalized intensity and tension values are clamped by the visual layer.
/// </summary>
public sealed record JarvisVisualSignals
{
    public JarvisState OperationalState { get; init; } = JarvisState.Idle;
    public double? ListeningAmplitude { get; init; }
    public double? SpeechAmplitude { get; init; }
    public double ThinkingIntensity { get; init; } = 1;
    public double ExecutionIntensity { get; init; } = 1;
    public double AlertSeverity { get; init; } = 1;
    public double MoodTension { get; init; }
    public int TaskCount { get; init; }
    public double MemoryUsage { get; init; } = 68;
    public string VisionStatus { get; init; } = "PASSIVE";
    public string CurrentUserTranscript { get; init; } = "Check what’s using my memory.";
    public string CurrentJarvisResponse { get; init; } = "Chrome is taking most of it. Nothing concerning.";
}
