# server.py — Jarvis Web UI Backend
# Serves the UI and exposes /api/chat for browser-based interaction.
# Run alongside jarvis.py (separate process) or standalone for UI-only mode.
#
# Usage:
#   pip install flask flask-cors
#   python server.py
#
# Defaults to http://localhost:5000

import os
import threading
import time
from datetime import datetime
import pyttsx3
import speech_recognition as sr
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

engine = pyttsx3.init()
recognizer = sr.Recognizer()

def speak(text: str):
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception:
        pass

# ── Lazy Jarvis imports (graceful if running headless / without PC deps) ───────
_planner    = None
_executor   = None
_jarvisify  = None
_import_err = None


def _load_jarvis():
    global _planner, _executor, _jarvisify, _import_err
    try:
        import planner
        import executor
        from jarvis import jarvisify_response

        # executor needs a no-op speak/ask for server mode
        def _noop_speak(text):
            pass

        executor.init(speak_fn=_noop_speak, ask_fn=planner.ask)
        _planner   = planner
        _executor  = executor
        _jarvisify = jarvisify_response
        print("[server] Jarvis modules loaded.")
    except Exception as exc:
        _import_err = str(exc)
        print(f"[server] WARNING: could not load Jarvis modules: {exc}")
        print("[server] Running in echo/demo mode.")


threading.Thread(target=_load_jarvis, daemon=True).start()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "ui"),
    static_url_path=""
)
CORS(app)

# In-memory chat log (resets on server restart)
_chat_log: list[dict] = []
_status = {"state": "idle", "updated": time.time()}
last_message = {"text": "", "role": ""}


def _set_status(state: str):
    _status["state"] = state
    _status["updated"] = time.time()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/status")
def api_status():
    """Returns current Jarvis state: idle | thinking | speaking."""
    return jsonify({
        "state":   _status["state"],
        "modules": "loaded" if _planner else "unavailable",
        "error":   _import_err,
        "time":    datetime.now().strftime("%H:%M:%S"),
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    POST {"message": "..."}
    Returns {"response": "...", "action": {...}|null, "model_type": "...", "timestamp": "..."}
    """
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()

    if not user_msg:
        return jsonify({"error": "Empty message."}), 400

    timestamp = datetime.now().strftime("%H:%M")
    _chat_log.append({"role": "user", "text": user_msg, "ts": timestamp})

    _set_status("thinking")

    try:
        if _planner is None:
            # Demo mode — echo back
            time.sleep(0.4)
            reply      = f"[Demo mode] Jarvis modules not loaded. You said: {user_msg}"
            action     = None
            model_type = "fast"
        else:
            action, reply, model_type = _planner.ask(user_msg)

            # If there's a real action, execute it
            if action and _executor:
                success, exec_msg = _executor.execute_with_retry(action)
                if exec_msg:
                    reply = exec_msg

            # Jarvisify the spoken reply
            if _jarvisify and reply:
                reply = _jarvisify(reply)

    except Exception as exc:
        print(f"[server] /api/chat error: {exc}")
        reply      = "Something went wrong on my end."
        action     = None
        model_type = "fast"

    _set_status("idle")
    _chat_log.append({"role": "jarvis", "text": reply, "ts": timestamp})

    last_message["text"] = reply
    last_message["role"] = "jarvis"

    speak(reply)

    return jsonify({
        "response":   reply or "",
        "action":     action,
        "model_type": model_type,
        "timestamp":  timestamp,
    })


@app.route("/api/history")
def api_history():
    """Returns recent chat history (last 50 turns)."""
    return jsonify({"history": _chat_log[-50:]})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """Clears in-memory chat log and planner history."""
    _chat_log.clear()
    if _planner:
        _planner.clear_history()
    return jsonify({"ok": True})


@app.route("/api/listen", methods=["GET"])
def api_listen():
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=5)

        text = recognizer.recognize_google(audio)
        return jsonify({"text": text})

    except Exception:
        return jsonify({"text": ""})


@app.route("/api/live")
def live():
    return jsonify(last_message)


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("JARVIS_PORT", 5000))
    print(f"[server] Starting Jarvis UI at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)