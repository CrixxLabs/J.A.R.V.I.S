using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using Jarvis.Desktop.Models;

namespace Jarvis.Desktop.Controls;

public sealed class AmbientField : FrameworkElement
{
    public static readonly DependencyProperty StateProperty = DependencyProperty.Register(
        nameof(State), typeof(JarvisState), typeof(AmbientField),
        new FrameworkPropertyMetadata(JarvisState.Idle, FrameworkPropertyMetadataOptions.AffectsRender));
    private static readonly SolidColorBrush ParticleBrush = FrozenBrush(Color.FromRgb(91, 207, 235));
    private static readonly Pen GridPen = FrozenPen(Color.FromArgb(9, 91, 190, 220), .5);
    private static readonly Pen TracePen = FrozenPen(Color.FromArgb(24, 72, 188, 220), .7);
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly Particle[] _particles;
    private double _lastInvalidate;
    private bool _subscribed;

    public AmbientField()
    {
        IsHitTestVisible = false;
        var random = new Random(719);
        _particles = Enumerable.Range(0, 42).Select(_ => new Particle(
            random.NextDouble(), random.NextDouble(),
            0.018 + random.NextDouble() * 0.045,
            0.25 + random.NextDouble() * 0.9,
            0.35 + random.NextDouble() * 0.75)).ToArray();
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    public JarvisState State
    {
        get => (JarvisState)GetValue(StateProperty);
        set => SetValue(StateProperty, value);
    }

    public double MoodTension { get; set; }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_subscribed) return;
        _lastInvalidate = _clock.Elapsed.TotalSeconds;
        CompositionTarget.Rendering += OnRendering;
        _subscribed = true;
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed) return;
        CompositionTarget.Rendering -= OnRendering;
        _subscribed = false;
    }

    private void OnRendering(object? sender, EventArgs e)
    {
        var now = _clock.Elapsed.TotalSeconds;
        var interval = State switch { JarvisState.Dormant => 1d / 10, JarvisState.Idle => 1d / 20, _ => 1d / 30 };
        if (now - _lastInvalidate < interval) return;
        _lastInvalidate = now;
        InvalidateVisual();
    }

    protected override void OnRender(DrawingContext dc)
    {
        var w = ActualWidth;
        var h = ActualHeight;
        if (w <= 0 || h <= 0) return;
        var t = _clock.Elapsed.TotalSeconds;

        dc.DrawRectangle(new LinearGradientBrush(
            Color.FromRgb(2, 7, 12), Color.FromRgb(3, 11, 18), 35), null, new Rect(0, 0, w, h));

        var center = new Point(w * .53, h * .5);
        var idleBreath = State == JarvisState.Idle ? .5 + .5 * Math.Sin(t * 1.48 + Math.Sin(t * .19) * .13) : .25;
        var glow = new RadialGradientBrush
        {
            Center = new Point(.5, .5), GradientOrigin = new Point(.5, .5), RadiusX = .58, RadiusY = .58,
            GradientStops = new GradientStopCollection
            {
                new(Color.FromArgb((byte)(22 + idleBreath * 8), 11, 87, 116), 0),
                new(Color.FromArgb((byte)(6 + idleBreath * 3), 4, 42, 64), .48),
                new(Colors.Transparent, 1)
            }
        };
        var fieldRadius = Math.Min(w, h) * (.615 + idleBreath * .008);
        dc.DrawEllipse(glow, null, center, fieldRadius, fieldRadius);

        const double grid = 48;
        var ox = (t * 2.2) % grid;
        dc.PushOpacity(State == JarvisState.Idle ? .72 + idleBreath * .18 : .76);
        for (double x = ox; x < w; x += grid) dc.DrawLine(GridPen, new Point(x, 54), new Point(x, h - 35));
        for (double y = 54; y < h - 35; y += grid) dc.DrawLine(GridPen, new Point(0, y), new Point(w, y));
        dc.Pop();

        for (var i = 0; i < _particles.Length; i++)
        {
            if (State == JarvisState.Dormant && i % 4 != 0) continue;
            var p = _particles[i];
            var y = ((p.Y - t * p.Speed * .012) % 1 + 1) % 1;
            var x = p.X + Math.Sin(t * p.Speed * (1 + MoodTension * .2) + p.Y * 13) * .008;
            var alpha = (12 + 30 * (.5 + .5 * Math.Sin(t * p.Speed + p.X * 19))) / 255;
            dc.PushOpacity(alpha);
            dc.DrawEllipse(ParticleBrush, null, new Point(x * w, y * h), p.Size, p.Size);
            if (p.Size > .75)
                dc.DrawLine(GridPen, new Point(x * w, y * h + 3), new Point(x * w, y * h + 15));
            dc.Pop();
        }

        var tx = ((t * 64) % (w + 250)) - 250;
        dc.DrawLine(TracePen, new Point(tx, h * .73), new Point(tx + 90, h * .73));
        dc.DrawLine(TracePen, new Point(tx + 90, h * .73), new Point(tx + 112, h * .73 - 12));
        dc.DrawLine(TracePen, new Point(tx + 112, h * .73 - 12), new Point(tx + 175, h * .73 - 12));
    }

    private static SolidColorBrush FrozenBrush(Color color) { var brush = new SolidColorBrush(color); brush.Freeze(); return brush; }
    private static Pen FrozenPen(Color color, double width) { var pen = new Pen(FrozenBrush(color), width); pen.Freeze(); return pen; }

    private sealed record Particle(double X, double Y, double Speed, double Size, double Phase);
}
