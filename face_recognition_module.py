# face_recognition_module.py — Phase 4A.1
# Webcam-based face recognition for Jarvis
# register_face() → save user encoding
# recognize_face() → check if user is present
# Non-blocking, lightweight, no UI

import os
import sys
import threading
import time
import pickle

import cv2
import numpy as np

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
ENCODING_FILE = os.path.join(BASE_DIR, "user_face.pkl")

# ── Shared state ──
_last_result     = False   # True = known user detected
_last_check_time = 0
_lock            = threading.Lock()
COOLDOWN_SECS    = 4       # check at most every 3s
_cap = None  # persistent camera (performance fix)
_last_printed_state = None

# ── Layer 3: Session tracking ──────────────────────────────────────────────────
_last_seen_time = 0.0   # timestamp of last successful recognition

# Lazy-loaded face_recognition
_face_recognition = None
_FR_AVAILABLE = None

def _ensure_face_recognition():
    """Lazily import face_recognition and check for models."""
    global _face_recognition, _FR_AVAILABLE
    if _face_recognition is not None:
        return _FR_AVAILABLE
    try:
        import face_recognition
        # Test if models are available
        face_recognition.face_encodings(np.zeros((100, 100, 3), dtype=np.uint8))
        _face_recognition = face_recognition
        _FR_AVAILABLE = True
    except Exception:
        _FR_AVAILABLE = False
    return _FR_AVAILABLE

# ───────────────────────────────────────────────────────────────────────────────
# ── Shared state ──
_last_result     = False   # True = known user detected
_last_check_time = 0
_lock            = threading.Lock()
COOLDOWN_SECS    = 4       # check at most every 3s
_cap = None  # persistent camera (performance fix)
_last_printed_state = None

# ── Layer 3: Session tracking ──────────────────────────────────────────────────
_last_seen_time = 0.0   # timestamp of last successful recognition


def minutes_since_last_seen() -> float:
    """
    Return how many minutes have passed since the user was last recognized.
    Returns a large number if user has never been seen.
    Used by jarvis.py to decide if this is a "return home" event.
    """
    with _lock:
        if _last_seen_time == 0:
            return 9999.0
        return (time.time() - _last_seen_time) / 60.0
# ───────────────────────────────────────────────────────────────────────────────


def register_face():
    """
    Capture user face from webcam and save encoding.
    Auto-captures after 3 seconds.
    """
    if not _FR_AVAILABLE:
        print("[FaceRec] face_recognition not available")
        return False

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[FaceRec] Camera not accessible")
        return False

    print("[FaceRec] Look at the camera. Capturing in 3 seconds...")

    encoding = None
    start_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        cv2.imshow("Registering Face...", frame)

        elapsed = time.time() - start_time

        if elapsed >= 3:
            print("[FaceRec] Capturing now...")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            encs = face_recognition.face_encodings(rgb)

            if encs:
                encoding = encs[0]
                print("[FaceRec] Face captured successfully.")
            else:
                print("[FaceRec] No face detected. Try again.")

            break

        cv2.waitKey(1)

    cap.release()
    cv2.destroyAllWindows()

    if encoding is not None:
        with open(ENCODING_FILE, "wb") as f:
            pickle.dump(encoding, f)
        print(f"[FaceRec] Saved encoding → {ENCODING_FILE}")
        return True

    print("[FaceRec] Registration failed.")
    return False


def _load_encoding():
    if not os.path.exists(ENCODING_FILE):
        return None
    try:
        with open(ENCODING_FILE, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        print(f"[FaceRec] load error: {e}")
        return None


def recognize_face(tolerance=0.6):
    """
    Capture one frame and check if the user matches saved encoding.
    Returns True if known user, False if unknown or no face.
    Non-blocking — uses cached result within cooldown period.
    """
    global _last_result, _last_check_time, _last_seen_time  # ← Layer 3 addition

    if not _FR_AVAILABLE:
        return False

    now = time.time()
    with _lock:
        if now - _last_check_time < COOLDOWN_SECS:
            return _last_result

    known = _load_encoding()
    if known is None:
        return False

    try:
        global _cap
        if _cap is None or not _cap.isOpened():
            _cap = cv2.VideoCapture(0)

        ret, frame = _cap.read()
        if not ret:
            return False

        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locs  = face_recognition.face_locations(rgb, model="hog")
        encs  = face_recognition.face_encodings(rgb, locs)

        result = False
        for enc in encs:
            distance = face_recognition.face_distance([known], enc)[0]
            if distance < tolerance:
                result = True
                break

        with _lock:
            _last_result     = result
            _last_check_time = now

            # ── Layer 3: update last-seen timestamp when user is recognized ────
            if result:
                _last_seen_time = now
            # ───────────────────────────────────────────────────────────────────

        global _last_printed_state

        current_state = "Known user" if result else "Unknown face"

        if current_state != _last_printed_state:
            print(f"[FaceRec] {current_state}")
            _last_printed_state = current_state
            return result

        return result  # ← added this so return happens even when state unchanged

    except Exception as e:
            print(f"[FaceRec] recognize error: {e}")
            return False


def is_face_registered():
    return os.path.exists(ENCODING_FILE)


# ── Background continuous watcher (optional) ──
# Runs in background so recognize_face() always has a fresh cached result
_watching = False
_watch_stop = threading.Event()
_watch_thread = None

def start_face_watcher():
    """Start a background thread that keeps the face cache warm."""
    global _watching, _watch_thread
    if _watching:
        return
    _watching = True
    _watch_stop.clear()

    def _watch():
        while not _watch_stop.is_set():
            try:
                recognize_face()
            except:
                pass
            _watch_stop.wait(COOLDOWN_SECS)

    _watch_thread = threading.Thread(target=_watch, daemon=True, name="face-watcher")
    _watch_thread.start()
    print("[FaceRec] Background watcher started.")


def stop_face_watcher():
    global _watching, _watch_thread
    _watching = False
    _watch_stop.set()
    if _watch_thread and _watch_thread.is_alive() and _watch_thread is not threading.current_thread():
        _watch_thread.join(timeout=3.0)
    _watch_thread = None
