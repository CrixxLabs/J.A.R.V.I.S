namespace Jarvis.Desktop.Models;

/// <summary>
/// Backend-facing visual contract. Null amplitudes mean no measured signal in production;
/// prototype motion is enabled only by the explicit DEV OVERRIDE toggle.
/// </summary>
public sealed record JarvisVisualSignals
{
    public JarvisState OperationalState { get; init; } = JarvisState.Idle;
    public double? ListeningAmplitude { get; init; }
    public double? SpeechAmplitude { get; init; }
    public double ThinkingIntensity { get; init; } = 1;
    public double ExecutionIntensity { get; init; } = 1;
    public double AlertSeverity { get; init; } = 1;
    public string Affect { get; init; } = "neutral";
    public double AffectTension { get; init; }
    public int? TaskCount { get; init; }
    public double? MemoryUsage { get; init; }
    public string? VisionStatus { get; init; }
    public string? ProviderModel { get; init; }
    public string? CurrentAction { get; init; }
    public string? CurrentUserTranscript { get; init; }
    public string? CurrentJarvisResponse { get; init; }
    public string? AlertInformation { get; init; }
    public long Sequence { get; init; }
}
