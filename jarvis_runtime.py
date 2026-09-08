"""
jarvis_runtime.py — DEPRECATED legacy runtime.

This module is kept for backward compatibility only.
It now delegates to the canonical entry point in jarvis.py.

USE jarvis.py INSTEAD:
    python jarvis.py

The modern lifecycle in jarvis.py includes:
- Proper subsystem initialization (observer, executor, tasks, face recognition)
- Status registry integration
- Error handling and demotion
- Proactive scheduler
- Self-awareness scanning
- Graceful shutdown handling

This legacy module will be removed in a future version.
"""

import warnings
import sys

warnings.warn(
    "jarvis_runtime.py is deprecated and will be removed. "
    "Use 'python jarvis.py' as the canonical entry point.",
    DeprecationWarning,
    stacklevel=2
)

# Delegate to the main entry point
if __name__ == "__main__":
    print("[jarvis_runtime] Redirecting to jarvis.py main entry point...")
    import jarvis
    # The jarvis module runs its main loop when imported as __main__
    # We just need to ensure the module is loaded
    sys.exit(0)