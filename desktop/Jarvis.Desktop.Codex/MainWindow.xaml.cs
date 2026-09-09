using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using Jarvis.Desktop.Models;
using Jarvis.Desktop.Services;

namespace Jarvis.Desktop;

public partial class MainWindow : Window
{
    private readonly JarvisStateController _stateController = new();
    private readonly JarvisRuntimeClient _runtimeClient = new();
    private JarvisVisualSignals? _latestRuntimeSignals;
    private bool _runtimeConnected;
    private bool _devOverride;

    public MainWindow()
    {
        InitializeComponent();
        _stateController.Changed += (_, state) => ApplyState(state);
        _runtimeClient.SnapshotReceived += (_, signals) => Dispatcher.InvokeAsync(() => ReceiveRuntimeSignals(signals));
        _runtimeClient.ConnectionChanged += (_, connected) => Dispatcher.InvokeAsync(() => SetRuntimeConnection(connected));
        StateChanged += (_, _) => UpdateMaximizeGlyph();
        Loaded += (_, _) => { _stateController.Set(JarvisState.Dormant); _runtimeClient.Start(); };
        Closed += async (_, _) => await _runtimeClient.DisposeAsync();
    }

    /// <summary>Single future integration point for backend state, telemetry, voice, and subtle mood modulation.</summary>
    public void ApplyVisualSignals(JarvisVisualSignals signals)
    {
        Core.SetInputAmplitude(signals.ListeningAmplitude);
        Core.SetSpeechAmplitude(signals.SpeechAmplitude);
        Core.SetModulation(signals.ThinkingIntensity, signals.ExecutionIntensity, signals.AlertSeverity, signals.AffectTension);
        Ambient.MoodTension = Math.Clamp(signals.AffectTension, 0, 1);
        if (!_devOverride) _stateController.Set(signals.OperationalState);
        TaskQueueValue.Text = Math.Clamp(signals.TaskCount ?? 0, 0, 99).ToString("00");
        MemoryValue.Text = signals.MemoryUsage.HasValue ? $"{Math.Clamp(signals.MemoryUsage.Value, 0, 100):0.0}%" : "--";
        VisionValue.Text = string.IsNullOrWhiteSpace(signals.VisionStatus) ? "UNWIRED" : signals.VisionStatus.ToUpperInvariant();
        UserTranscript.Text = signals.CurrentUserTranscript ?? "";
        JarvisResponse.Text = signals.CurrentJarvisResponse ?? "";
        if (!string.IsNullOrWhiteSpace(signals.ProviderModel) && signals.OperationalState == JarvisState.Thinking)
            CognitiveStatus.Text = signals.ProviderModel.ToUpperInvariant();
        AnalysisHeader.Text = string.IsNullOrWhiteSpace(signals.CurrentAction)
            ? "SYSTEM ANALYSIS  /  RUNTIME TASK"
            : $"SYSTEM ANALYSIS  /  {signals.CurrentAction.ToUpperInvariant()}";
        AnalysisValues.Visibility = Visibility.Collapsed;
        AnalysisProcesses.Visibility = Visibility.Collapsed;
    }

    private void ReceiveRuntimeSignals(JarvisVisualSignals signals)
    {
        _latestRuntimeSignals = signals;
        ApplyVisualSignals(signals);
    }

    private void SetRuntimeConnection(bool connected)
    {
        _runtimeConnected = connected;
        RuntimeOwnership.Text = connected ? "RUNTIME / LIVE" : "RUNTIME / WAITING";
        RuntimeOwnership.Foreground = new SolidColorBrush(connected ? Color.FromRgb(88, 174, 193) : Color.FromRgb(82, 105, 114));
        if (!connected && !_devOverride) _stateController.Set(JarvisState.Dormant);
    }

    private void State_OnChecked(object sender, RoutedEventArgs e)
    {
        if (_devOverride && sender is RadioButton { Tag: string name } && Enum.TryParse<JarvisState>(name, out var state))
            _stateController.Set(state);
    }

    private void DevOverride_OnChanged(object sender, RoutedEventArgs e)
    {
        _devOverride = DevOverrideToggle.IsChecked == true;
        if (_devOverride)
        {
            RuntimeOwnership.Text = "VISUAL / OVERRIDE";
            RuntimeOwnership.Foreground = new SolidColorBrush(Color.FromRgb(214, 169, 84));
            AnalysisValues.Visibility = Visibility.Visible;
            AnalysisProcesses.Visibility = Visibility.Visible;
            IdleStateChoice.IsChecked = true;
            _stateController.Set(JarvisState.Idle);
        }
        else
        {
            RuntimeOwnership.Text = _runtimeConnected ? "RUNTIME / LIVE" : "RUNTIME / WAITING";
            if (_latestRuntimeSignals is not null) ApplyVisualSignals(_latestRuntimeSignals);
            else _stateController.Set(JarvisState.Dormant);
        }
    }

    private void ApplyState(JarvisState state)
    {
        Core.State = state;
        Ambient.State = state;
        StateReadout.Text = string.Join(" ", state.ToString().ToUpperInvariant().ToCharArray());
        StateDetail.Text = state switch
        {
            JarvisState.Dormant => "CORE  QUIESCENT",
            JarvisState.Listening => "VOICE  ACQUISITION",
            JarvisState.Thinking => "COGNITIVE  SYNTHESIS",
            JarvisState.Speaking => "RESPONSE  CHANNEL",
            JarvisState.Executing => "TASK  PROPAGATION",
            JarvisState.Alert => "FAULT  ISOLATION",
            _ => "CORE  SYNCHRONIZED"
        };
        TaskQueueValue.Text = state == JarvisState.Executing ? "03" : "00";
        var color = state switch
        {
            JarvisState.Executing => Color.FromRgb(239, 186, 83),
            JarvisState.Alert => Color.FromRgb(239, 78, 86),
            JarvisState.Dormant => Color.FromRgb(68, 91, 99),
            _ => Color.FromRgb(120, 209, 231)
        };
        StateReadout.Foreground = new SolidColorBrush(color);
        SystemStatus.Text = state switch
        {
            JarvisState.Alert => "●  ATTENTION REQUIRED",
            JarvisState.Dormant => "○  LOW POWER STATE",
            JarvisState.Executing => "●  ACTIVE EXECUTION",
            _ => "●  SYSTEM NOMINAL"
        };
        SystemStatus.Foreground = new SolidColorBrush(Color.FromArgb(190, color.R, color.G, color.B));
        VoiceStatus.Text = state switch { JarvisState.Listening => "RECEIVING", JarvisState.Speaking => "OUTPUT", JarvisState.Dormant => "QUIET", _ => "STANDBY" };
        CognitiveStatus.Text = state switch { JarvisState.Thinking => "ROUTING", JarvisState.Alert => "ISOLATING", JarvisState.Dormant => "PASSIVE", _ => "ONLINE" };
        VisionValue.Foreground = new SolidColorBrush(state == JarvisState.Alert ? Color.FromRgb(221, 91, 97) : Color.FromRgb(215, 234, 240));
        TaskQueueValue.Foreground = new SolidColorBrush(state == JarvisState.Executing ? Color.FromRgb(239, 186, 83) : Color.FromRgb(215, 234, 240));
        // Peripheral telemetry behaves as one nervous system: only the relevant path wakes.
        Fade(VoiceLinkPanel, state is JarvisState.Listening or JarvisState.Speaking ? 1 : state == JarvisState.Dormant ? .24 : .48, 420);
        Fade(CognitivePanel, state == JarvisState.Thinking ? 1 : state == JarvisState.Alert ? .72 : .48, 460);
        Fade(TelemetryPanel, state is JarvisState.Executing or JarvisState.Alert ? .94 : state == JarvisState.Dormant ? .25 : .52, 460);
        Fade(IdentityPanel, state == JarvisState.Dormant ? .42 : .88, 500);
        Fade(LocalCorePanel, state == JarvisState.Thinking ? .95 : state == JarvisState.Dormant ? .3 : .62, 420);
        Fade(VoiceConnection, state == JarvisState.Listening ? .24 : state == JarvisState.Speaking ? .16 : 0, 380);
        Fade(CognitiveConnection, state == JarvisState.Thinking ? .2 : state == JarvisState.Alert ? .1 : 0, 420);
        Fade(TaskConnection, state == JarvisState.Executing ? .34 : 0, 430);
        Fade(ConversationConnection, state is JarvisState.Listening or JarvisState.Speaking ? .14 : 0, 400);
        AnimateElement(AnalysisPanel, state == JarvisState.Executing ? 1 : 0, state == JarvisState.Executing ? 0 : 8, 360);
        if (state == JarvisState.Executing)
        {
            AnimateOpacity(AnalysisHeader, 0, 1, 250, 90);
            AnimateOpacity(AnalysisValues, 0, 1, 280, 170);
            AnimateOpacity(AnalysisProcesses, 0, 1, 320, 260);
        }
        AnimateElement(ConversationPanel, state is JarvisState.Listening or JarvisState.Speaking or JarvisState.Executing ? .86 : .34, 0, 420);
        if (state == JarvisState.Listening)
        {
            Materialize(UserExchange, .98, .55);
            Fade(JarvisExchange, .2, 300);
        }
        else if (state == JarvisState.Speaking)
        {
            Fade(UserExchange, .4, 300);
            Materialize(JarvisExchange, 1, .62);
        }
        else
        {
            Fade(UserExchange, state == JarvisState.Executing ? .48 : .38, 380);
            Fade(JarvisExchange, state == JarvisState.Executing ? .9 : .58, 380);
        }
        var width = state switch { JarvisState.Thinking => 132, JarvisState.Executing => 142, JarvisState.Dormant => 34, _ => 99 };
        CoreLoadBar.BeginAnimation(WidthProperty, new DoubleAnimation(width, TimeSpan.FromMilliseconds(550)) { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } });
    }

    private static void AnimateElement(UIElement element, double opacity, double y, int milliseconds)
    {
        var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
        element.BeginAnimation(OpacityProperty, new DoubleAnimation(opacity, TimeSpan.FromMilliseconds(milliseconds)) { EasingFunction = ease });
        if (element.RenderTransform is TranslateTransform transform)
            transform.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(y, TimeSpan.FromMilliseconds(milliseconds)) { EasingFunction = ease });
    }

    private static void AnimateOpacity(UIElement element, double from, double to, int milliseconds, int delay)
    {
        element.BeginAnimation(OpacityProperty, new DoubleAnimation(from, to, TimeSpan.FromMilliseconds(milliseconds))
        {
            BeginTime = TimeSpan.FromMilliseconds(delay),
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        });
    }

    private static void Fade(UIElement element, double opacity, int milliseconds) =>
        element.BeginAnimation(OpacityProperty, new DoubleAnimation(opacity, TimeSpan.FromMilliseconds(milliseconds))
        { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } });

    private static void Materialize(UIElement element, double peak, double resting)
    {
        var animation = new DoubleAnimationUsingKeyFrames { Duration = TimeSpan.FromSeconds(4.8) };
        animation.KeyFrames.Add(new EasingDoubleKeyFrame(peak, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(360)))
        { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } });
        animation.KeyFrames.Add(new LinearDoubleKeyFrame(peak, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(2.8))));
        animation.KeyFrames.Add(new EasingDoubleKeyFrame(resting, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(4.8)))
        { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut } });
        element.BeginAnimation(OpacityProperty, animation);
    }

    private void TitleBar_OnMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ClickCount == 2) { ToggleMaximize(); return; }
        if (e.ButtonState == MouseButtonState.Pressed) DragMove();
    }
    private void Minimize_OnClick(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void Maximize_OnClick(object sender, RoutedEventArgs e) => ToggleMaximize();
    private void Close_OnClick(object sender, RoutedEventArgs e) => Close();
    private void ToggleMaximize() => WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
    private void UpdateMaximizeGlyph() => MaximizeButton.Content = WindowState == WindowState.Maximized ? "❐" : "□";
}
