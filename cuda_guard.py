
import os
import sys

# ══════════════════════════════════════════════════════════════════════════════
# CUDA ALLOCATION HARDENING — Platform-specific guard
# ══════════════════════════════════════════════════════════════════════════════
if sys.platform != "win32":
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
else:
    # On Windows, expandable_segments is not supported
    if "PYTORCH_CUDA_ALLOC_CONF" in os.environ:
        del os.environ["PYTORCH_CUDA_ALLOC_CONF"]
