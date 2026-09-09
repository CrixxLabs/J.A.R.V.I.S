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
import uuid
import asyncio
import mimetypes
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.utils import secure_filename

import pyttsx3
import speech_recognition as sr
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

from task_queue import TaskPriority
from file_processor import get_processor, process_file

load_dotenv()

# Security configuration
ALLOWED_ORIGINS = os.getenv("JARVIS_CORS_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000").split(",")
BIND_HOST = os.getenv("JARVIS_BIND_HOST", "127.0.0.1")
REQUIRE_AUTH = os.getenv("JARVIS_REQUIRE_AUTH", "false").lower() == "true"
API_KEY = os.getenv("JARVIS_API_KEY", "")  # Optional API key for privileged actions

engine = pyttsx3.init()
recognizer = sr.Recognizer()

def speak(text: str):
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception:
        pass

def _require_auth(f):
    """Decorator to require API key for privileged endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not REQUIRE_AUTH or not API_KEY:
            return f(*args, **kwargs)
        provided = request.headers.get("X-API-Key") or request.args.get("api_key")
        if provided != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# File upload configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx',
                      '.html', '.css', '.json', '.xml', '.yaml', '.yml', '.csv', '.tsv',
                      '.xlsx', '.xls', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
                      '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.mp4', '.mov', '.avi',
                      '.mkv', '.webm', '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar'}

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
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=False)

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
@_require_auth
def api_clear():
    """Clears in-memory chat log and planner history."""
    _chat_log.clear()
    if _planner:
        _planner.clear_history()
    return jsonify({"ok": True})


@app.route("/api/listen", methods=["GET"])
@_require_auth
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


# ── File Processing Endpoints ────────────────────────────────────────────────

def _allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@app.route("/api/upload", methods=["POST"])
@_require_auth
def api_upload():
    """Upload file(s) for processing."""
    if 'files' not in request.files and 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    files = request.files.getlist('files') if 'files' in request.files else [request.files['file']]
    if not files or all(f.filename == '' for f in files):
        return jsonify({"error": "No file selected"}), 400
    
    # Ensure upload directory exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    uploaded = []
    for file in files:
        if file.filename == '':
            continue
        
        if not _allowed_file(file.filename):
            return jsonify({"error": f"File type not allowed: {file.filename}"}), 400
        
        # Generate safe filename
        filename = secure_filename(file.filename)
        file_id = str(uuid.uuid4())[:8]
        ext = Path(file.filename).suffix.lower()
        safe_filename = f"{file_id}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, safe_filename)
        
        # Save file
        file.save(filepath)
        
        # Check file size
        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE:
            os.remove(filepath)
            return jsonify({"error": f"File too large: {file_size} bytes (max {MAX_FILE_SIZE})"}), 413
        
        # Get file info
        mime_type, _ = mimetypes.guess_type(filepath)
        
        uploaded.append({
            "id": file_id,
            "original_name": file.filename,
            "saved_name": safe_filename,
            "size": file_size,
            "mime_type": mime_type or "application/octet-stream",
            "path": filepath
        })
    
    return jsonify({"files": uploaded, "count": len(uploaded)})


@app.route("/api/files", methods=["GET"])
@_require_auth
def api_files():
    """List uploaded files."""
    if not os.path.exists(UPLOAD_FOLDER):
        return jsonify({"files": []})
    
    files = []
    for fname in os.listdir(UPLOAD_FOLDER):
        filepath = os.path.join(UPLOAD_FOLDER, fname)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            mime_type, _ = mimetypes.guess_type(filepath)
            files.append({
                "name": fname,
                "size": stat.st_size,
                "mime_type": mime_type or "application/octet-stream",
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    
    return jsonify({"files": files})


@app.route("/api/files/<file_id>", methods=["DELETE"])
@_require_auth
def api_delete_file(file_id: str):
    """Delete an uploaded file."""
    if not os.path.exists(UPLOAD_FOLDER):
        return jsonify({"error": "File not found"}), 404
    
    for fname in os.listdir(UPLOAD_FOLDER):
        if fname.startswith(file_id + "_"):
            filepath = os.path.join(UPLOAD_FOLDER, fname)
            os.remove(filepath)
            return jsonify({"ok": True, "deleted": fname})
    
    return jsonify({"error": "File not found"}), 404


@app.route("/api/process-file", methods=["POST"])
@_require_auth
def api_process_file():
    """
    Process an uploaded file with a specific action.
    POST {"file_id": "...", "action": "summarize|extract|ocr|transcribe|analyze|code_review|debug|inspect|summarize|explain|convert", "params": {...}}
    """
    data = request.get_json(silent=True) or {}
    file_id = data.get("file_id")
    action = data.get("action", "inspect")
    params = data.get("params", {})
    
    if not file_id:
        return jsonify({"error": "file_id required"}), 400
    
    # Find file
    filepath = None
    for fname in os.listdir(UPLOAD_FOLDER) if os.path.exists(UPLOAD_FOLDER) else []:
        if fname.startswith(file_id + "_"):
            filepath = os.path.join(UPLOAD_FOLDER, fname)
            break
    
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    
    # Submit to task queue for async processing
    try:
        from task_queue import submit_task
        
        def _coro_factory():
            return _process_file_task(filepath, action, params)
        
        task_id = asyncio.run(submit_task(
            _coro_factory,
            name=f"file_{action}_{file_id}",
            priority=TaskPriority.NORMAL
        ))
        return jsonify({"task_id": task_id, "status": "queued"})
    except Exception as e:
        return jsonify({"error": f"Failed to queue task: {e}"}), 500


async def _process_file_task(filepath: str, action: str, params: dict):
    """Process file asynchronously using file_processor."""
    try:
        from file_processor import process_file
        result = process_file(filepath, action, **params)
        return {
            "success": result.success,
            "action": action,
            "result": result.result,
            "error": result.error,
            "metadata": {
                "file": result.metadata.name if result.metadata else None,
                "size": result.metadata.size if result.metadata else None,
                "mime": result.metadata.mime_type if result.metadata else None
            } if result.metadata else None
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.route("/api/tasks/<task_id>", methods=["GET"])
@_require_auth
def api_task_status(task_id: str):
    """Get status of a background task."""
    try:
        from task_queue import get_task_status
        status = asyncio.run(get_task_status(task_id))
        if status is None:
            return jsonify({"error": "Task not found"}), 404
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tasks", methods=["GET"])
@_require_auth
def api_list_tasks():
    """List recent tasks."""
    try:
        from task_queue import get_task_queue
        queue = get_task_queue()
        stats = queue.get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vision/status", methods=["GET"])
@_require_auth
def api_vision_status():
    """Get vision subsystem status."""
    try:
        from vision import get_vision_status
        status = get_vision_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/activity", methods=["GET"])
@_require_auth
def api_activity():
    """Get recent activity from memory."""
    try:
        from memory import get_recent_activity
        activities = get_recent_activity(20)
        items = []
        for act in activities:
            act_type = act.get("type", "unknown")
            detail = act.get("detail", "")
            time_str = act.get("time", "")[:16].replace("T", " ") if act.get("time") else ""
            items.append({
                "time": time_str,
                "type": act_type,
                "text": f"{act_type}: {detail[:80]}"
            })
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/subsystems", methods=["GET"])
@_require_auth
def api_subsystems():
    """Get all subsystem statuses."""
    try:
        from status_registry import get_registry
        registry = get_registry()
        all_status = registry.get_all()
        return jsonify(all_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/process-file-sync", methods=["POST"])
@_require_auth
def api_process_file_sync():
    """
    Process file synchronously (for quick operations).
    POST {"file_id": "...", "action": "inspect|extract|summarize|..."}
    """
    data = request.get_json(silent=True) or {}
    file_id = data.get("file_id")
    action = data.get("action", "inspect")
    params = data.get("params", {})
    
    if not file_id:
        return jsonify({"error": "file_id required"}), 400
    
    # Find file
    filepath = None
    for fname in os.listdir(UPLOAD_FOLDER) if os.path.exists(UPLOAD_FOLDER) else []:
        if fname.startswith(file_id + "_"):
            filepath = os.path.join(UPLOAD_FOLDER, fname)
            break
    
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    
    try:
        from file_processor import process_file
        result = process_file(filepath, action, **params)
        return jsonify({
            "success": result.success,
            "action": action,
            "result": result.result,
            "error": result.error,
            "metadata": {
                "file": result.metadata.name if result.metadata else None,
                "size": result.metadata.size if result.metadata else None,
                "mime": result.metadata.mime_type if result.metadata else None
            } if result.metadata else None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("JARVIS_PORT", 5000))
    print(f"[server] Starting Jarvis UI at http://{BIND_HOST}:{port}")
    print(f"[server] CORS origins: {ALLOWED_ORIGINS}")
    if REQUIRE_AUTH:
        print("[server] API authentication ENABLED")
    app.run(host=BIND_HOST, port=port, debug=False, threaded=True)