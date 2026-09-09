using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using Jarvis.Desktop.Models;

namespace Jarvis.Desktop.Controls;

/// <summary>State-driven visual core. Amplitude setters accept future normalized voice/TTS data; null restores simulation.</summary>
public sealed class JarvisCore : FrameworkElement
{
    public static readonly DependencyProperty StateProperty = DependencyProperty.Register(nameof(State), typeof(JarvisState), typeof(JarvisCore),
        new FrameworkPropertyMetadata(JarvisState.Idle, FrameworkPropertyMetadataOptions.AffectsRender, StateChanged));
    private static readonly Dictionary<ArcKey, StreamGeometry> Arcs = new();
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private JarvisState _previous = JarvisState.Idle;
    private Profile _from = Profile.For(JarvisState.Idle), _to = Profile.For(JarvisState.Idle);
    private double _transitionStart, _lastFrame, _lastInvalidate, _input, _speech;
    private double? _inputOverride, _speechOverride;
    private double _thinkingIntensity = 1, _executionIntensity = 1, _alertSeverity = 1, _moodTension;
    private bool _subscribed;

    public JarvisCore()
    {
        IsHitTestVisible = false;
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    public JarvisState State { get => (JarvisState)GetValue(StateProperty); set => SetValue(StateProperty, value); }
    public double InputAmplitude => _input;
    public double SpeechAmplitude => _speech;
    public double TransitionProgress { get; private set; } = 1;
    public void SetInputAmplitude(double? value) => _inputOverride = Normalize(value);
    public void SetSpeechAmplitude(double? value) => _speechOverride = Normalize(value);
    public void SetModulation(double thinkingIntensity, double executionIntensity, double alertSeverity, double moodTension)
    {
        _thinkingIntensity = Math.Clamp(thinkingIntensity, 0, 1);
        _executionIntensity = Math.Clamp(executionIntensity, 0, 1);
        _alertSeverity = Math.Clamp(alertSeverity, 0, 1);
        _moodTension = Math.Clamp(moodTension, 0, 1);
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_subscribed) return;
        _lastFrame = _lastInvalidate = _clock.Elapsed.TotalSeconds;
        CompositionTarget.Rendering += RenderFrame;
        _subscribed = true;
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed) return;
        CompositionTarget.Rendering -= RenderFrame;
        _subscribed = false;
    }

    private static void StateChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var core = (JarvisCore)d;
        var now = core._clock.Elapsed.TotalSeconds;
        core._from = core.CurrentProfile(now); // preserves continuity even if changed mid-transition
        core._to = Profile.For((JarvisState)e.NewValue);
        core._previous = (JarvisState)e.OldValue;
        core._transitionStart = now;
    }

    private void RenderFrame(object? sender, EventArgs e)
    {
        var now = _clock.Elapsed.TotalSeconds;
        var interval = State switch { JarvisState.Dormant => 1d / 15, JarvisState.Idle => 1d / 30, _ => 1d / 60 };
        if (now - _lastInvalidate < interval) return;
        var dt = Math.Clamp(now - _lastFrame, 0, .05);
        _lastFrame = _lastInvalidate = now;
        var inputTarget = _inputOverride ?? SimulatedInput(now);
        var speechTarget = _speechOverride ?? SimulatedSpeech(now);
        _input = Follow(_input, inputTarget, dt, inputTarget > _input ? 14 : 6);
        _speech = Follow(_speech, speechTarget, dt, speechTarget > _speech ? 18 : 8);
        InvalidateVisual();
    }

    protected override void OnRender(DrawingContext dc)
    {
        if (ActualWidth < 10 || ActualHeight < 10) return;
        var t = _clock.Elapsed.TotalSeconds;
        var p = CurrentProfile(t);
        p = p with
        {
            Compute = p.Compute * _thinkingIntensity,
            Burst = p.Burst * _executionIntensity,
            Alert = p.Alert * _alertSeverity,
            Speed = p.Speed * (1 + _moodTension * .16),
            Brightness = p.Brightness * (1 + _moodTension * .06)
        };
        var breath = OrganicBreath(t) * p.Breath;
        var voice = p.Listen * _input + p.Speech * _speech;
        var energy = Math.Clamp(breath + voice * .72 + p.Compute * .22 + p.Burst * .18, 0, 1.5);
        var color = Color.FromRgb(p.R, p.G, p.B);
        var ink = TransitionProgress > .999 ? Ink.Cached(color) : new Ink(color);
        var unit = Math.Min(ActualWidth, ActualHeight) / 590.0;
        dc.PushTransform(new TranslateTransform(ActualWidth / 2, ActualHeight / 2));
        dc.PushTransform(new ScaleTransform(unit, unit));
        Atmosphere(dc, t, p, ink, energy);
        Depth(dc, t, p, ink, breath);
        OuterTicks(dc, t, p, ink, voice);
        TelemetryArcs(dc, t, p, ink);
        ReceptiveField(dc, t, p, ink);
        SegmentRing(dc, t, p, ink, voice);
        InnerMechanism(dc, t, p, ink);
        Nodes(dc, t, p, ink);
        Execution(dc, t, p, ink);
        Alert(dc, t, p);
        Center(dc, p, ink, energy);
        dc.Pop(); dc.Pop();
    }

    private Profile CurrentProfile(double t)
    {
        TransitionProgress = Ease(Math.Clamp((t - _transitionStart) / Duration(_previous, State), 0, 1));
        return Lerp(_from, _to, TransitionProgress);
    }

    private static void Atmosphere(DrawingContext dc, double t, Profile p, Ink ink, double energy)
    {
        var r = 205 + energy * 8 + Math.Sin(t * .31) * 1.5;
        var glow = new RadialGradientBrush(new GradientStopCollection {
            new(Color.FromArgb((byte)(20*p.Brightness+energy*8),p.R,p.G,p.B),0),
            new(Color.FromArgb((byte)(7*p.Brightness+energy*4),p.R,p.G,p.B),.5), new(Colors.Transparent,1)});
        dc.DrawEllipse(glow, null, new Point(), r, r);
        Line(dc, ink, 44*p.Brightness, 1, new(-255,0), new(-220,0)); Line(dc, ink, 44*p.Brightness, 1, new(220,0), new(255,0));
        Line(dc, ink, 44*p.Brightness, 1, new(0,-255), new(0,-220)); Line(dc, ink, 44*p.Brightness, 1, new(0,220), new(0,255));
    }

    private static void Depth(DrawingContext dc, double t, Profile p, Ink ink, double breath)
    {
        Ellipse(dc, ink, (16+breath*11)*p.Brightness, .65, 202+breath*3);
        dc.PushTransform(new RotateTransform(t*1.7*p.Speed));
        for (var i=0;i<4;i++) Arc(dc,ink,18*p.Brightness,.65,246,18+i*90,17);
        dc.Pop();
    }

    private static void OuterTicks(DrawingContext dc, double t, Profile p, Ink ink, double voice)
    {
        dc.PushTransform(new RotateTransform(t*(1.7+p.Speed*1.9)));
        for (var i=0;i<120;i++) {
            // Broad, irregular gaps stop this layer reading as a radar scale.
            if ((i is > 17 and < 25 || i is > 63 and < 70 || i is > 96 and < 104) && voice < .22) continue;
            var major=i%10==0; var active=(i+(int)(t*(8+p.Speed*9)))%31<3;
            var sector=.48+.52*Noise(i/4+37); var local=voice*(.3+.7*Noise(i*17+(int)(t*13)));
            Radial(dc,ink,((active?92:major?48:21)*sector+local*95)*p.Brightness,major?1.2:.65,i*3,(major?213:220)-local*4,(major?235:229)+local*9);
        }
        dc.Pop();
    }

    private static void TelemetryArcs(DrawingContext dc,double t,Profile p,Ink ink)
    {
        dc.PushTransform(new RotateTransform(-t*(3+p.Speed*4.2)));
        Arc(dc,ink,70*p.Brightness,1,194,0,63); Arc(dc,ink,32*p.Brightness,.8,194,104,23); Arc(dc,ink,53*p.Brightness,1.4,194,188,81); dc.Pop();
        var cadence=1+p.Compute*.22*SmoothNoise(t*.8+4); dc.PushTransform(new RotateTransform(t*(6+p.Speed*6)*cadence));
        Arc(dc,ink,67*p.Brightness,1,176,24,49); Arc(dc,ink,29*p.Brightness,.7,176,117,112);
        for(var i=0;i<8;i++) Radial(dc,ink,48*p.Brightness,.7,i*45,181,191); dc.Pop();
        dc.PushTransform(new RotateTransform(-t*(2+p.Speed*2.2))); Arc(dc,ink,50*p.Brightness,1,156,210,105); dc.Pop();
    }

    private void ReceptiveField(DrawingContext dc,double t,Profile p,Ink ink)
    {
        var amount=Math.Max(p.Listen,p.Speech); if(amount<.015)return;
        var global=p.Listen*_input+p.Speech*_speech;
        for(var i=0;i<48;i++) {
            var v=.25+.75*Noise(i*29+(int)(t*(p.Speech>p.Listen?17:11))); var amp=Math.Clamp(global*v+amount*.08,0,1); var outer=254+amp*10;
            Radial(dc,ink,(25+amp*105)*p.Brightness*amount,1.05,i*7.5,outer-(5+amp*24),outer);
        }
        if(p.Listen>.02) for(var i=0;i<6;i++) {
            var phase=(t*(.48+i*.017)+i*.163)%1; var peak=Math.Clamp((_input-.28)*1.5,0,1); var r=260-phase*105; var a=i*60+Noise(i*37)*18-9;
            Radial(dc,ink,145*(1-phase)*peak*p.Listen,1.2,a,r,r-13);
        }
    }

    private static void SegmentRing(DrawingContext dc,double t,Profile p,Ink ink,double voice)
    {
        var cadence=1+p.Compute*.35*SmoothNoise(t*1.2+2); dc.PushTransform(new RotateTransform(t*(8+p.Speed*8)*cadence));
        for(var i=0;i<24;i++) { var hi=(i+(int)(t*(4+p.Speed*3)))%12<2; var response=voice*(.3+.7*Noise(i*43+(int)(t*15)));
            Arc(dc,ink,((hi?132:49)+response*74)*p.Brightness,hi?2.1:1.05,133,i*15,9.5);
            if(i%3==0) Radial(dc,ink,(46+response*50)*p.Brightness,.8,i*15+5,124-response*3,130+response*3); }
        dc.Pop(); dc.PushTransform(new RotateTransform(-t*(5+p.Speed*5.5))); for(var i=0;i<6;i++) Arc(dc,ink,68*p.Brightness,1.1,112,i*60,36); dc.Pop();
    }

    private void InnerMechanism(DrawingContext dc,double t,Profile p,Ink ink)
    {
        var voice=p.Speech*_speech; var life=.82+p.Brightness*.18;
        var compression=p.Listen*5+p.Compute*4+(1-p.Brightness)*9;
        var speed=(7+p.Speed*13)*(1+p.Compute*.18*SmoothNoise(t*1.7));
        dc.PushTransform(new RotateTransform(-t*speed));
        for(var i=0;i<8;i++) {
            var a=i*45.0; var correction=(SmoothNoise(t*.23+i*4.1)-.5)*2.2*p.Breath;
            var speechDeform=voice*(Noise(i*47+(int)(t*12))-.28)*9;
            var executionPush=p.Burst*Math.Max(0,1-Math.Abs(i-3.2)/2.4)*9;
            var tension=p.Alert*(i%3==0?5:-2)+_moodTension*(i%2==0?2.4:-1.2);
            var inner=(72-compression+correction+speechDeform*.35)*life;
            var outer=(92-compression+speechDeform+executionPush+tension)*life;
            Line(dc,ink,64*p.Brightness,1,Polar(a,inner),Polar(a+27,outer));
            Line(dc,ink,64*p.Brightness,1,Polar(a+27,outer),Polar(a+45,inner));
        }
        dc.Pop(); dc.PushTransform(new RotateTransform(t*(15+p.Speed*16))); Arc(dc,ink,88*p.Brightness,1.6,72-compression,0,128); Arc(dc,ink,39*p.Brightness,.8,72-compression,170,58); dc.Pop();
        if(p.Compute>.02) for(var i=0;i<5;i++) { var phase=(t*(.67+i*.051)+i*.21)%1; var r=148-phase*63; var a=i*72-t*(17+i*2); Dot(dc,ink,175*(1-phase*.45)*p.Compute,Polar(a,r),1.1+p.Compute); Radial(dc,ink,80*(1-phase)*p.Compute,.75,a,r+13,r); }
    }

    private static void Nodes(DrawingContext dc,double t,Profile p,Ink ink)
    {
        for(var i=0;i<7;i++) { var a=i*51.43+t*(5+i%3*2+p.Speed*4)*(i%2==0?1:-1); var r=148+(i%3)*16-p.Compute*(i%2)*13; var pulse=.35+.65*SmoothNoise(t*(1.1+i*.07)+i*3.7); var pt=Polar(a,r);
            Dot(dc,ink,(42+pulse*118)*p.Brightness,pt,1.3+pulse); if(p.Data>.18&&i%2==0) Line(dc,ink,42*p.Data*p.Brightness,.8,Polar(a-11*p.Data,r),pt); }
    }

    private static void Execution(DrawingContext dc,double t,Profile p,Ink ink)
    {
        if(p.Burst<.015)return;
        for(var i=0;i<9;i++) { var phase=(t*(.38+i*.013)+i*.137)%1; var r=52+phase*214; var fade=(1-phase)*p.Burst; var angle=i is 3 or 4?118+(i-3)*13:i*40+Noise(i*31)*16-8; var end=Polar(angle,r+19+phase*18); Line(dc,ink,180*fade,1.3,Polar(angle,r),end); Dot(dc,ink,190*fade,end,2); }
        var pulse=.55+.45*SmoothNoise(t*2.1); Radial(dc,ink,92*p.Burst,.8,127,174,270); Radial(dc,ink,185*p.Burst*pulse,1.5,127,192+pulse*44,207+pulse*44);
    }

    private static void Alert(DrawingContext dc,double t,Profile p)
    {
        if(p.Alert<.015)return; var red=Ink.Cached(Color.FromRgb(239,67,76)); var pulse=.55+.45*SmoothNoise(t*3.4); dc.PushTransform(new RotateTransform(t*24));
        Arc(dc,red,185*p.Alert*pulse,1.6,194,12,31); Arc(dc,red,120*p.Alert,1.1,194,184,18); Arc(dc,red,155*p.Alert,1.4,133,274,22); dc.Pop();
        for(var i=0;i<3;i++) Dot(dc,red,(120+pulse*90)*p.Alert,Polar(24+i*121-t*8,165),1.8);
    }

    private static void Center(DrawingContext dc,Profile p,Ink ink,double energy)
    {
        var r=29*(1+energy*.055); var halo=new RadialGradientBrush(new GradientStopCollection {
            new(Color.FromArgb((byte)Math.Clamp(142+energy*48,0,225),220,249,255),0), new(Color.FromArgb((byte)Math.Clamp(52+energy*35,0,150),p.R,p.G,p.B),.22), new(Color.FromArgb((byte)Math.Clamp(8+energy*10,0,42),p.R,p.G,p.B),.62), new(Colors.Transparent,1)});
        dc.DrawEllipse(halo,null,new Point(),r*2.75,r*2.75); FillEllipse(dc,ink,38*p.Brightness,176*p.Brightness,1.4,new Point(),r); Dot(dc,ink,Math.Min(235,185*p.Brightness+energy*30),new Point(),4.4+energy*2.1);
        Line(dc,ink,92*p.Brightness,.7,new(-18,0),new(18,0)); Line(dc,ink,92*p.Brightness,.7,new(0,-18),new(0,18));
    }

    private static void Arc(DrawingContext dc,Ink ink,double alpha,double width,double radius,double start,double sweep) { dc.PushOpacity(Math.Clamp(alpha/255,0,1)); dc.DrawGeometry(null,ink.Pen(width),GetArc(radius,start,sweep)); dc.Pop(); }
    private static void Radial(DrawingContext dc,Ink ink,double alpha,double width,double angle,double inner,double outer)=>Line(dc,ink,alpha,width,Polar(angle,inner),Polar(angle,outer));
    private static void Line(DrawingContext dc,Ink ink,double alpha,double width,Point a,Point b) { dc.PushOpacity(Math.Clamp(alpha/255,0,1)); dc.DrawLine(ink.Pen(width),a,b); dc.Pop(); }
    private static void Ellipse(DrawingContext dc,Ink ink,double alpha,double width,double radius) { dc.PushOpacity(Math.Clamp(alpha/255,0,1)); dc.DrawEllipse(null,ink.Pen(width),new Point(),radius,radius); dc.Pop(); }
    private static void Dot(DrawingContext dc,Ink ink,double alpha,Point p,double radius) { dc.PushOpacity(Math.Clamp(alpha/255,0,1)); dc.DrawEllipse(ink.Brush,null,p,radius,radius); dc.Pop(); }
    private static void FillEllipse(DrawingContext dc,Ink ink,double fill,double stroke,double width,Point p,double r) { dc.PushOpacity(Math.Clamp(fill/255,0,1)); dc.DrawEllipse(ink.Brush,null,p,r,r); dc.Pop(); dc.PushOpacity(Math.Clamp(stroke/255,0,1)); dc.DrawEllipse(null,ink.Pen(width),p,r,r); dc.Pop(); }

    private static StreamGeometry GetArc(double r,double start,double sweep)
    {
        var key=new ArcKey(Math.Round(r,2),Math.Round(start,2),Math.Round(sweep,2)); if(Arcs.TryGetValue(key,out var found))return found;
        var g=new StreamGeometry(); using(var c=g.Open()) { c.BeginFigure(Polar(start,r),false,false); c.ArcTo(Polar(start+sweep,r),new Size(r,r),0,sweep>180,SweepDirection.Clockwise,true,false); } g.Freeze(); Arcs[key]=g; return g;
    }
    private static Point Polar(double degrees,double radius) { var a=degrees*Math.PI/180; return new(Math.Sin(a)*radius,-Math.Cos(a)*radius); }
    private static double SimulatedInput(double t) { var pause=Noise((int)(t/.72)+91)>.2?1:.13; return Math.Clamp((.09+SmoothNoise(t*7.4+13)*.66+SmoothNoise(t*2.2+41)*.2)*pause,.04,1); }
    private static double SimulatedSpeech(double t) { var x=t%8.6; var gate=Phrase(x,.15,1.12)+Phrase(x,1.38,2.72)+Phrase(x,3.15,4.02)+Phrase(x,4.48,6.78)+Phrase(x,7.18,7.82); gate=Math.Clamp(gate,0,1); return gate<.01?.025:Math.Clamp(gate*(.16+SmoothNoise(t*11.7+73)*.62+SmoothNoise(t*3.1+117)*.24),.03,1); }
    private static double Phrase(double x,double a,double b)=>SmoothStep(a,a+.11,x)*(1-SmoothStep(b-.11,b,x));
    private static double OrganicBreath(double t) { var wave=.5+.5*Math.Sin(t*1.48+Math.Sin(t*.19)*.13); return .18+.82*SmoothStep(0,1,wave); }
    private static double SmoothNoise(double x) { var n=(int)Math.Floor(x); var f=x-n; f=f*f*(3-2*f); return Noise(n)+(Noise(n+1)-Noise(n))*f; }
    private static double Noise(int n) { unchecked { uint x=(uint)n; x^=x>>16; x*=0x7feb352d; x^=x>>15; x*=0x846ca68b; x^=x>>16; return (x&0xffffff)/16777215d; } }
    private static double Follow(double current,double target,double dt,double speed)=>current+(target-current)*(1-Math.Exp(-speed*dt));
    private static double? Normalize(double? value)=>value.HasValue?Math.Clamp(value.Value,0,1):null;
    private static double SmoothStep(double a,double b,double x) { var v=Math.Clamp((x-a)/(b-a),0,1); return v*v*(3-2*v); }
    private static double Duration(JarvisState from,JarvisState to)=>to switch { JarvisState.Alert=>.28,JarvisState.Executing=>.62,JarvisState.Dormant=>.7,_ when from==JarvisState.Speaking&&to==JarvisState.Idle=>.68,_=>.48 };
    private static double Ease(double x)=>x<.5?4*x*x*x:1-Math.Pow(-2*x+2,3)/2;
    private static double L(double a,double b,double x)=>a+(b-a)*x;
    private static Profile Lerp(Profile a,Profile b,double x)=>new(L(a.Brightness,b.Brightness,x),L(a.Speed,b.Speed,x),L(a.Breath,b.Breath,x),L(a.Listen,b.Listen,x),L(a.Compute,b.Compute,x),L(a.Speech,b.Speech,x),L(a.Data,b.Data,x),L(a.Burst,b.Burst,x),L(a.Alert,b.Alert,x),(byte)L(a.R,b.R,x),(byte)L(a.G,b.G,x),(byte)L(a.B,b.B,x));
    private readonly record struct ArcKey(double Radius,double Start,double Sweep);
    private sealed class Ink { private static readonly Dictionary<int,Ink> Cache=new(); private readonly Dictionary<double,Pen> _pens=new(); public Ink(Color c){Brush=new SolidColorBrush(c);Brush.Freeze();} public SolidColorBrush Brush{get;} public static Ink Cached(Color c){var key=(c.R<<16)|(c.G<<8)|c.B;if(!Cache.TryGetValue(key,out var ink)){ink=new Ink(c);Cache[key]=ink;}return ink;} public Pen Pen(double w){if(_pens.TryGetValue(w,out var p))return p;p=new Pen(Brush,w){StartLineCap=PenLineCap.Round,EndLineCap=PenLineCap.Round};p.Freeze();_pens[w]=p;return p;} }
    private readonly record struct Profile(double Brightness,double Speed,double Breath,double Listen,double Compute,double Speech,double Data,double Burst,double Alert,byte R,byte G,byte B)
    { public static Profile For(JarvisState s)=>s switch { JarvisState.Dormant=>new(.13,.05,.08,0,0,0,0,0,0,68,110,122),JarvisState.Listening=>new(.88,.68,.12,1,.06,0,.28,0,0,96,220,246),JarvisState.Thinking=>new(.92,2.05,.06,.04,1,.04,1,.08,0,98,214,242),JarvisState.Speaking=>new(1,1,.08,0,.08,1,.48,0,0,127,226,248),JarvisState.Executing=>new(1,1.48,.08,0,.22,.03,1,1,0,244,179,72),JarvisState.Alert=>new(.67,1.72,.03,0,.48,0,.52,.08,1,85,164,184),_=>new(.62,.31,.72,0,0,0,.12,0,0,91,207,235)}; }
}
