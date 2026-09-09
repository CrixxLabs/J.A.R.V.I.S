using Jarvis.Desktop.Models;

namespace Jarvis.Desktop.Services;

public sealed class JarvisStateController
{
    public JarvisState Current { get; private set; } = JarvisState.Idle;

    public event EventHandler<JarvisState>? Changed;

    public void Set(JarvisState state)
    {
        if (state == Current) return;
        Current = state;
        Changed?.Invoke(this, state);
    }
}
