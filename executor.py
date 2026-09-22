# executor.py — Action Executor
# Executes all actions from planner
# Handles multi-step sequences + safety
import subprocess
import webbrowser
try:
    import pyautogui
except Exception as _pyautogui_err:
    pyautogui = None
    print(f"[executor] pyautogui unavailable: {_pyautogui_err}")
import psutil
import datetime
import os
import json
import glob
import re
import time
import ipaddress
import socket
from urllib.parse import urljoin, urlparse, parse_qs
import threading
import pyperclip
import smtplib
try:
    import pywhatkit
except Exception as _pywhatkit_err:
    pywhatkit = None
    print(f"[executor] pywhatkit unavailable: {_pywhatkit_err}")
import requests
try:
    from mss import mss
except Exception:
    mss = None
try:
    import cv2
except Exception:
    cv2 = None
import numpy as np
from bs4 import BeautifulSoup
try:
    from ddgs import DDGS
except Exception:
    DDGS = None
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None
from dotenv import load_dotenv
from memory import remember, recall_all, log_activity, update_daily_stats, log_failure

# Dev Agent (MARK VII)
try:
    from dev_agent import DEV_AGENT_ACTIONS
    _DEV_AGENT_AVAILABLE = True
except Exception as _dev_err:
    _DEV_AGENT_AVAILABLE = False
    print(f"[executor] Dev Agent not available: {_dev_err}")

# File ops module — provides search_files and rename_file (lazy import to avoid cycles)
def _get_file_ops():
    from file_ops import search_files, rename_file
    return search_files, rename_file

# Email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Reliability imports
import status_registry
import runtime_visuals
from status_registry import EvidenceLevel, SubsystemState, get_registry
import error_handler

# Spotify (safe optional)
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except Exception:
    spotipy = None

# Google Calendar (safe optional)
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow
except Exception:
    Credentials = Request = build = InstalledAppFlow = None

load_dotenv()
BASE_DIR              = os.path.dirname(os.path.abspath(__file__))
WEATHER_API_KEY       = os.getenv("WEATHER_API_KEY", "")
NEWS_API_KEY          = os.getenv("NEWS_API_KEY", "")
GMAIL_ADDRESS         = os.getenv("GMAIL_ADDRESS", "")
GMAIL_PASSWORD        = os.getenv("GMAIL_PASSWORD", "")
SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
GCAL_TOKEN_FILE       = os.path.join(BASE_DIR, "gcal_token.json")
GCAL_CREDS_FILE       = os.path.join(BASE_DIR, "credentials.json")
GCAL_SCOPES           = ["https://www.googleapis.com/auth/calendar"]
API_KEY               = os.getenv("OPENROUTER_API_KEY", "")


def _require_pyautogui():
    if pyautogui is None:
        raise RuntimeError("Windows UI automation is unavailable in this environment")
    return pyautogui


def _discover_tesseract() -> str | None:
    """Compatibility wrapper around canonical Tesseract discovery."""
    from ocr_runtime import probe_tesseract
    return probe_tesseract().path

# Injected callbacks
_speak_fn  = None
_ask_fn    = None

# Safety: actions requiring confirmation
CONFIRM_ACTIONS = {"shutdown_pc", "restart_pc"}
_pending_confirm = None
_CONFIRM_TTL = float(os.getenv("JARVIS_CONFIRM_TTL", "20"))

def init(speak_fn, ask_fn):
    global _speak_fn, _ask_fn
    _speak_fn = speak_fn
    _ask_fn   = ask_fn


# ── AVAILABLE_ACTIONS — Brain-Body sync (Phase 4A.2) ──
AVAILABLE_ACTIONS = {
    "system_control": [
        "lock PC", "shutdown PC", "restart PC",
        "check CPU / RAM / battery", "get current time and date",
        "scroll up / down", "click", "take screenshot and describe it"
    ],
    "apps": [
        "open any app by name", "open files by name",
        "list files in a folder", "type text into any input",
        "install apps via winget with real-time progress",
        "auto-login to supported apps with saved credentials",
    ],
    "media": [
        "play / pause / skip music", "volume up / down / mute",
        "play a song on Spotify by name", "like current Spotify track",
        "next / previous Spotify track"
    ],
    "vision": [
        "read text on screen (OCR)", "describe screen using AI vision",
        "summarize a YouTube video by URL", "summarize any article by URL"
    ],
    "productivity": [
        "set reminders and timers", "morning briefing (weather + calendar + news)",
        "check Google Calendar events today", "add Google Calendar event",
        "web search for anything", "read / write clipboard"
    ],
    "communication": [
        "send WhatsApp message", "send Gmail email", "check weather for any city",
        "read top news headlines"
    ],
    "memory": [
        "remember facts about Arju", "recall saved facts",
        "track what apps Arju uses", "log daily activity"
    ],
    "generation": [
        "image generation status (currently unavailable)",
        "experimental video generation status"
    ],
    "obligations": [
        "add assignment / exam / deadline obligation",
        "query pending and overdue obligations",
        "scan portal screen for deadlines",
        "mark obligation as done",
    ],
    "profile": [
        "view user profile summary",
        "remember a fact about the user",
        "forget a profile entry",
    ],
    "self_awareness": [
        "scan own codebase and detect abilities",
        "report changes since last boot",
    ],
    "credentials": [
        "save login credentials securely (Windows DPAPI)",
        "list saved app credentials",
        "delete saved credentials for an app",
    ],
}

AVAILABLE_ACTIONS_LIST = [
    "open_app", "close_app", "join_meeting", "system_control", "play_music",
    "send_message", "read_screen", "web_search",
    "type_text", "media", "scroll", "generate_image",
    "generate_video", "click", "screenshot_describe",
    "system_info", "system_status", "datetime", "summarize_url", "remember", "recall",
    "weather", "news", "set_reminder", "send_whatsapp", "send_email",
    "lock_pc", "shutdown_pc", "restart_pc", "morning_briefing",
    "voice_type", "clipboard_read", "clipboard_write",
    "spotify_play", "spotify_control", "calendar_today", "calendar_add",
    "open_file", "list_folder", "search_file", "rename_file", "run_sequence",
    "whatsapp_download", "whatsapp_read", "whatsapp_timetable_update", "exit",
    # Layer 1 — Obligation Engine
    "add_obligation", "query_obligations", "portal_scan", "mark_obligation_done",
    # Self-diagnostic
    "run_diagnostic",
    # User profile
    "profile_query", "profile_forget", "profile_remember",
    # Iron Man automation
    "install_app",              # install an app via winget
    "install_and_login",        # install + open + auto-login
    "open_and_login",           # just launch + auto-login (app already installed)
    "save_login",               # save credentials for an app
    "list_logins",              # show what credentials are saved
    "delete_login",             # remove saved credentials for an app
    "self_scan",                # rescan own codebase
    "self_capabilities",        # report current abilities
    "self_changes",             # report what changed since last boot
    # File Processor (MARK VII)
    "process_file",             # universal file processing (inspect, summarize, extract, ocr, transcribe, analyze, code_review, debug, convert)
    "list_uploaded_files",      # list files in upload directory

    # Vision/Eyes (MARK VII)
    "capture_webcam",           # capture and describe webcam frame
    "capture_screen_region",    # capture and describe screen region
    "analyze_image_file",       # analyze image file with Gemini Vision
    "read_image_text",          # OCR text extraction from image file
    "vision_status",            # get vision subsystem status

    # Dev Agent (MARK VII)
    "dev_inspect",           # inspect file structure and content
    "dev_test",              # run tests and analyze failures
    "dev_search",            # search code for patterns
    "dev_propose",           # propose code changes (diff)
    "dev_status",            # get dev agent status
    # Declarative fact handling (MARK VII)
    "declarative_fact",      # store declarative fact statements
    "fact_query",            # query specific facts from profile
]

def get_capabilities_text():
    """Returns a short confident capabilities summary for the system prompt."""
    lines = []
    for category, items in AVAILABLE_ACTIONS.items():
        lines.append(f"{category.replace('_',' ').title()}: {', '.join(items[:3])}{'...' if len(items) > 3 else ''}")
    return "\n".join(lines)


# ── Spotify ──
_spotify = None
def get_spotify():
    global _spotify
    registry = get_registry()
    if _spotify:
        return _spotify
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        registry.set_status("SPOTIFY", SubsystemState.DISABLED, "SPOTIFY_CLIENT_ID or SECRET missing in .env")
        return None
    try:
        auth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-read-playback-state user-modify-playback-state user-library-modify",
            cache_path=os.path.join(BASE_DIR, ".spotify_cache")
        )
        _spotify = spotipy.Spotify(auth_manager=auth)
        registry.set_evidence("SPOTIFY", EvidenceLevel.PROBED, "Spotify OAuth client initialized",
                              source="Spotify OAuth")
        registry.set_capability_evidence("SPOTIFY", EvidenceLevel.PROBED,
                                         "Spotify OAuth client initialized", source="Spotify OAuth")
        return _spotify
    except Exception as e:
        error_handler.log_and_demote("SPOTIFY", e, "Spotify client connection initialization", SubsystemState.DEGRADED)
        return None

def spotify_play(query):
    sp = get_spotify()
    if not sp:
        return "Spotify is not configured. Add your Spotify client ID and secret to the .env file."
    try:
        results = sp.search(q=query, limit=1, type="track")
        tracks  = results.get("tracks", {}).get("items", [])
        if not tracks:
            return f"Couldn't find '{query}' on Spotify."
        t       = tracks[0]
        devices = sp.devices().get("devices", [])
        if not devices:
            return "No active Spotify devices found. Please open Spotify on your PC first."
        sp.start_playback(device_id=devices[0]["id"], uris=[t["uri"]])
        time.sleep(1)
        log_activity("spotify_play", t["name"])
        registry = get_registry()
        registry.set_evidence("SPOTIFY", EvidenceLevel.LIVE, "Playback request succeeded",
                              source="Spotify playback")
        registry.set_capability_evidence("SPOTIFY", EvidenceLevel.LIVE,
                                         "Playback request succeeded", source="Spotify playback")
        return f"Playing {t['name']} by {t['artists'][0]['name']}."
    except Exception as e:
        error_handler.log_and_demote("SPOTIFY", e, f"Spotify search and play for query '{query}'", SubsystemState.DEGRADED)
        return "I had trouble starting playback on Spotify."

def spotify_control(cmd):
    sp = get_spotify()
    if not sp:
        return "Spotify is not connected."
    try:
        if cmd == "pause":   sp.pause_playback();   return "Paused Spotify."
        if cmd == "resume":  sp.start_playback();   return "Resuming Spotify."
        if cmd == "next":    sp.next_track();       return "Skipping to next track."
        if cmd == "prev":    sp.previous_track();   return "Going back to previous track."
        if cmd == "like":
            cur = sp.current_playback()
            if cur and cur.get("item"):
                sp.current_user_saved_tracks_add([cur["item"]["id"]])
                return f"Liked {cur['item']['name']}."
            return "Nothing is currently playing on Spotify."
        if cmd == "current":
            cur = sp.current_playback()
            if cur and cur.get("item"):
                return f"Currently playing {cur['item']['name']} by {cur['item']['artists'][0]['name']}."
            return "Nothing is currently playing on Spotify."
        return "Invalid Spotify control command."
    except Exception as e:
        error_handler.log_and_demote("SPOTIFY", e, f"Spotify command direct execution: '{cmd}'", SubsystemState.DEGRADED)
        return "Spotify control action failed."


# ── Google Calendar ──
def get_cal_service():
    registry = get_registry()
    if not os.path.exists(GCAL_CREDS_FILE) and not os.path.exists(GCAL_TOKEN_FILE):
        registry.set_status("CALENDAR", SubsystemState.DISABLED, "Calendar credential files missing")
        return None
    try:
        creds = None
        if os.path.exists(GCAL_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(GCAL_TOKEN_FILE, GCAL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists(GCAL_CREDS_FILE):
                flow  = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS_FILE, GCAL_SCOPES)
                creds = flow.run_local_server(port=0)
            if creds:
                with open(GCAL_TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
        if creds:
            svc = build("calendar", "v3", credentials=creds)
            registry.set_evidence("CALENDAR", EvidenceLevel.PROBED,
                                  "OAuth credentials accepted and API client built",
                                  source="Calendar authentication")
            registry.set_capability_evidence("CALENDAR", EvidenceLevel.PROBED,
                                             "OAuth credentials accepted and API client built",
                                             source="Calendar authentication")
            return svc
        return None
    except Exception as e:
        error_handler.log_and_demote("CALENDAR", e, "Establishing Google Calendar endpoint auth", SubsystemState.DEGRADED)
        return None

def calendar_today():
    try:
        svc = get_cal_service()
        if not svc:
            return "Google Calendar is not connected. Please add your credentials.json file."
        now = datetime.datetime.utcnow().isoformat() + "Z"
        end = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat() + "Z"
        evts = svc.events().list(calendarId="primary", timeMin=now, timeMax=end,
                                  maxResults=5, singleEvents=True, orderBy="startTime"
                                  ).execute().get("items", [])
        if not evts:
            return "You have nothing on your calendar today."
        lines = []
        for e in evts:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            t = datetime.datetime.fromisoformat(start).strftime("%I:%M %p") if "T" in start else "all day"
            lines.append(f"{e['summary']} at {t}")
        return "Today on your calendar: " + ", ".join(lines) + "."
    except Exception as e:
        error_handler.log_and_demote("CALENDAR", e, "Querying calendar primary resource events", SubsystemState.DEGRADED)
        return "I couldn't fetch your calendar events."

def calendar_add(title, date_str, time_str="09:00"):
    try:
        svc = get_cal_service()
        if not svc:
            return "Google Calendar is not connected."
        dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = dt + datetime.timedelta(hours=1)
        svc.events().insert(calendarId="primary", body={
            "summary": title,
            "start":   {"dateTime": dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end":     {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }).execute()
        log_activity("calendar_add", title)
        return f"Successfully added '{title}' to your calendar."
    except Exception as e:
        error_handler.log_and_demote("CALENDAR", e, f"Inserting calendar entity '{title}'", SubsystemState.DEGRADED)
        return "I couldn't add that event to your calendar."


# ── Web & content ──
def web_search(query):
    if DDGS is None:
        return "Web search is unavailable because the ddgs package is not installed."
    try:
        hits = DDGS(timeout=10).text(query, max_results=4)
        results = [str(hit.get("body", "")).strip() for hit in hits if hit.get("body")]

        if not results:
            return "I couldn't find anything on that."

        raw_snippets = " | ".join(results)[:1500]
        raw_snippets = re.sub(r"\s+", " ", raw_snippets)
        raw_snippets = raw_snippets.replace(" | ", ". ")

        summary = _summarize_source(raw_snippets,
                                    f"Answer this web search for {query} in 2-3 sentences.")
        if summary:
            return summary

        return raw_snippets[:400]

    except Exception as e:
        print(f"[Search] {e}")
        return "Search failed."


def _summarize_source(text: str, instruction: str) -> str:
    """Summarize retrieved text at the provider boundary, outside planner/plugins."""
    if not text.strip():
        return ""
    try:
        import brain
        result = brain.ask_llm(f"{instruction}\n\nSource text:\n{text[:6000]}", allow_actions=False)
        if result.startswith("I can't reach an AI provider"):
            return ""
        return result.strip()
    except Exception as exc:
        print(f"[Summary] {type(exc).__name__}")
        return ""

def get_weather(city):
    try:
        if not WEATHER_API_KEY:
            data = web_search(f"weather in {city} today")
            resp = _ask_fn(f"Give a natural weather update for {city}:\n{data}")
            r = resp[1] if isinstance(resp, tuple) else resp
            return r
        d = requests.get(
            f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric",
            timeout=8).json()
        if d.get("cod") != 200:
            return f"couldn't get weather for {city}"
        return (f"it's {d['main']['temp']:.0f} degrees in {city}. "
                f"feels like {d['main']['feels_like']:.0f}. "
                f"{d['weather'][0]['description']}. humidity {d['main']['humidity']}%.")
    except Exception as e:
        print(f"[Weather] {e}")
        return "weather fetch failed"

def get_news():
    try:
        if not NEWS_API_KEY:
            data = web_search("top news headlines today")
            resp = _ask_fn(f"Give 3 quick headlines naturally:\n{data}")
            r = resp[1] if isinstance(resp, tuple) else resp
            return r
        arts = requests.get(
            f"https://newsapi.org/v2/top-headlines?language=en&pageSize=5&apiKey={NEWS_API_KEY}",
            timeout=8).json().get("articles", [])
        if not arts:
            return "no headlines right now"
        return "here's what's going on — " + ". Next, ".join([a["title"] for a in arts[:3] if a.get("title")])
    except Exception as e:
        print(f"[News] {e}")
        return "news fetch failed"


# ── Morning briefing ──
def morning_briefing() -> str:
    now      = datetime.datetime.now()
    greeting = f"Good morning Arju. It's {now.strftime('%I:%M %p')} on {now.strftime('%A, %d %B')}."
    weather  = get_weather(os.getenv("JARVIS_HOME_CITY", "Bengaluru"))
    cal      = calendar_today()
    news     = get_news()
    log_activity("morning_briefing", "completed")
    return f"{greeting} Weather — {weather}. Calendar — {cal}. News — {news}."


# ── OCR + Screenshot ──
def read_screen_text():
    from vision import read_screen
    return read_screen()


# ── File manager ──
def find_file(name):
    dirs = [os.path.expanduser(f"~/{d}") for d in ["Desktop","Documents","Downloads","Music","Pictures","Videos"]]
    for d in dirs:
        matches = glob.glob(os.path.join(d, f"*{name}*"))
        if matches:
            return matches[0]
    return None

def open_file(name):
    path = find_file(name)
    if path:
        os.startfile(path)
        log_activity("open_file", os.path.basename(path))
        return f"opening {os.path.basename(path)}."
    return f"couldn't find {name}."

def list_folder(folder="Downloads"):
    folder_map = {d: os.path.expanduser(f"~/{d.capitalize()}") for d in
                  ["desktop","documents","downloads","music","pictures","videos"]}
    path  = folder_map.get(folder.lower(), os.path.expanduser("~/Downloads"))
    files = os.listdir(path)[:8]
    if not files:
        return f"nothing in {folder}."
    return "top files in " + folder + " — " + ", ".join(files[:6])


# ── Email + WhatsApp ──
def send_email(to, subject, body):
    registry = get_registry()
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        registry.set_status("EMAIL", SubsystemState.DISABLED, "GMAIL_ADDRESS or PASSWORD not set in .env")
        return "Email is not set up. Add your GMAIL_ADDRESS and GMAIL_PASSWORD to your .env file."
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"]   = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            srv.sendmail(GMAIL_ADDRESS, to, msg.as_string())
        log_activity("email_sent", to)
        registry.set_evidence("EMAIL", EvidenceLevel.LIVE, "SMTP delivery call completed",
                              source="email send")
        registry.set_capability_evidence("GMAIL", EvidenceLevel.LIVE,
                                         "SMTP delivery call completed", source="email send")
        return f"Email sent successfully to {to}."
    except Exception as e:
        error_handler.log_and_demote("EMAIL", e, f"Gmail SMTP mailing to {to}", SubsystemState.DEGRADED)
        return "Email failed to send. Check SMTP settings."

def send_whatsapp(phone, message):
    registry = get_registry()
    if pywhatkit is None:
        registry.set_status("WHATSAPP_SEND", SubsystemState.DISABLED, "pywhatkit is not installed")
        return "WhatsApp sending is unavailable because pywhatkit is not installed."
    try:
        now = datetime.datetime.now()
        pywhatkit.sendwhatmsg(phone, message, now.hour, now.minute + 2, wait_time=15, tab_close=True)
        log_activity("whatsapp_sent", phone)
        registry.set_evidence("WHATSAPP_SEND", EvidenceLevel.LIVE,
                              "PyWhatKit accepted the browser send operation",
                              source="WhatsApp send")
        registry.set_capability_evidence("WHATSAPP_SEND", EvidenceLevel.LIVE,
                                         "PyWhatKit accepted the browser send operation",
                                         source="WhatsApp send")
        return f"WhatsApp queued to {phone}."
    except Exception as e:
        error_handler.log_and_demote("WHATSAPP_SEND", e, f"pywhatkit messaging pipeline to {phone}", SubsystemState.DEGRADED)
        return "Couldn't send WhatsApp message."


# ── PC control ──
def lock_pc():     subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
def shutdown_pc(): subprocess.run(["shutdown", "/s", "/t", "5"], check=False)
def restart_pc():  subprocess.run(["shutdown", "/r", "/t", "5"], check=False)


# ── Clipboard ──
def clipboard_read():
    try:
        text = pyperclip.paste()
        return f"clipboard has: {text[:200]}" if text else "clipboard is empty"
    except Exception as e:
        print(f"[executor error] {e}")
        return "Couldn't read clipboard."

def clipboard_write(text):
    try:
        pyperclip.copy(text)
        return "copied to clipboard."
    except Exception as e:
        print(f"[executor error] {e}")
        return "Couldn't write to clipboard."


# ── YouTube ──
def get_youtube_id(url):
    """Extract a YouTube video id from common watch/share/short/embed/live URLs."""
    try:
        parsed = urlparse(str(url).strip())
        host = (parsed.hostname or "").lower()
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = parsed.path.strip("/").split("/")[0]
        elif host.endswith("youtube.com"):
            if parsed.path == "/watch":
                candidate = (parse_qs(parsed.query).get("v") or [""])[0]
            else:
                parts = [p for p in parsed.path.split("/") if p]
                candidate = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"} else ""
        else:
            return None
        candidate = re.sub(r"[^A-Za-z0-9_-]", "", candidate)
        return candidate if 6 <= len(candidate) <= 32 else None
    except Exception:
        return None

def _fetch_youtube_transcript(video_id: str) -> str:
    """Support both current and legacy youtube-transcript-api interfaces."""
    if YouTubeTranscriptApi is None:
        raise RuntimeError("youtube-transcript-api is not installed")
    class _BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", (3, 12))
            return super().request(method, url, **kwargs)

    session = _BoundedSession()
    api = YouTubeTranscriptApi(http_client=session)
    if hasattr(api, "fetch"):
        try:
            transcript = api.fetch(video_id)
            chunks = [getattr(seg, "text", None) or (seg.get("text", "") if isinstance(seg, dict) else "") for seg in transcript]
        finally:
            session.close()
    else:
        session.close()
        raise RuntimeError("youtube-transcript-api >= 1.2 is required for bounded transcript fetching")
    return " ".join(c.strip() for c in chunks if c and c.strip())

def summarize_youtube(url):
    try:
        vid = get_youtube_id(url)
        if not vid:
            return "That doesn't look like a supported YouTube video URL."
        txt = _fetch_youtube_transcript(vid)[:12000]
        if not txt.strip():
            return "The video has no accessible transcript."
        return _summarize_source(txt, "Summarize this YouTube transcript in under 5 sentences.") or "Transcript found, but summarization is unavailable."
    except Exception as e:
        print(f"[YouTube] {type(e).__name__}: {e}")
        return "I couldn't retrieve an accessible transcript for that video."

def _is_public_http_url(url: str) -> bool:
    """Reject local/file/private targets before article fetching."""
    try:
        parsed = urlparse(str(url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
            return False
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            return False
        for addr in addresses:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
        return True
    except Exception:
        return False

def summarize_article(url):
    try:
        current_url = str(url).strip()
        res = None
        for _ in range(6):
            if not _is_public_http_url(current_url):
                return "I can only summarize public HTTP or HTTPS pages."
            res = requests.get(
                current_url,
                headers={"User-Agent": "Mozilla/5.0 (JARVIS MARK VII)"},
                timeout=(3, 10),
                allow_redirects=False,
                stream=True,
            )
            if res.status_code not in {301, 302, 303, 307, 308}:
                break
            location = res.headers.get("Location")
            if not location:
                res.close()
                return "That page returned an invalid redirect."
            res.close()
            current_url = urljoin(current_url, location)
        else:
            return "That page redirected too many times."

        if res is None:
            return "I had trouble reading that page."
        res.raise_for_status()
        ctype = (res.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text/" not in ctype:
            res.close()
            return "That URL doesn't appear to contain readable article text."
        declared_length = res.headers.get("Content-Length")
        if declared_length and int(declared_length) > 2_000_000:
            res.close()
            return "That page is too large to summarize safely."
        chunks = []
        total = 0
        for chunk in res.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > 2_000_000:
                res.close()
                return "That page is too large to summarize safely."
            chunks.append(chunk)
        encoding = res.encoding or "utf-8"
        res.close()
        page_text = b"".join(chunks).decode(encoding, errors="replace")
        soup = BeautifulSoup(page_text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        txt = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()
        if len(txt) < 80:
            return "I couldn't extract enough readable article text from that page."
        return _summarize_source(txt[:12000], "Summarize this article in under 5 sentences using only the supplied source text.") or "Article read, but summarization is unavailable."
    except Exception as e:
        print(f"[Article] {type(e).__name__}: {e}")
        return "I had trouble reading that page."


def _extract_class_subject(text):
    t = (text or "").lower()
    if "excel" in t:
        return "Excel"
    if ("ai" in t and "ml" in t) or ("artificial intelligence" in t and "machine learning" in t):
        return "AI/ML"
    if "machine learning" in t or re.search(r'\bml\b', t):
        return "ML"
    if "artificial intelligence" in t or re.search(r'\bai\b', t):
        return "AI"
    match = re.search(r'([A-Za-z0-9/& ]{2,40})\s+(class|session|revision|lecture)', text or "", re.IGNORECASE)
    if match:
        candidate = match.group(1).strip(" -:")
        words = [w for w in candidate.split() if w.lower() not in ("advanced", "exam", "join", "meet", "today")]
        if words:
            return " ".join(words[-2:]).strip().title()
    return "your"


def _extract_class_time(text):
    match = re.search(r'\b(\d{1,2}[:.]\d{2}\s?(?:am|pm)?)\b', text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r'\b(\d{1,2}\s?(?:am|pm))\b', text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _join_google_meet(link):
    if not link:
        return False
    try:
        webbrowser.open(link)
        time.sleep(6)
        _require_pyautogui().hotkey("ctrl", "d")
        time.sleep(0.3)
        _require_pyautogui().hotkey("ctrl", "e")
        time.sleep(1)
        for _ in range(10):
            _require_pyautogui().press("tab")
            time.sleep(0.15)
        _require_pyautogui().press("enter")
        return True
    except Exception as e:
        print(f"[Meet Join Error] {e}")
        return False


CHROME_PATH = os.getenv(
    "CHROME_PATH",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)

APP_PATHS_STABLE = {
    "chrome": CHROME_PATH,
    "google chrome": CHROME_PATH,
    "whatsapp": os.path.expandvars(r"%LocalAppData%\WhatsApp\WhatsApp.exe"),
    "notepad": "notepad.exe",
    "cmd": "cmd.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
}

PROCESS_ALIASES_STABLE = {
    "chrome": {"chrome.exe"},
    "google chrome": {"chrome.exe"},
    "whatsapp": {"whatsapp.exe"},
    "notepad": {"notepad.exe"},
    "cmd": {"cmd.exe", "windowsterminal.exe"},
    "calculator": {"calculatorapp.exe", "calc.exe"},
    "calc": {"calculatorapp.exe", "calc.exe"},
}


def _stable_success(message=""):
    return True, message


def _stable_failure(message=""):
    return False, message


def _stable_from_text(message, failure_markers=()):
    """Convert string-returning integrations into truthful executor success/failure."""
    text = str(message or "").strip()
    lowered = text.lower()
    markers = tuple(m.lower() for m in failure_markers)
    if not text or any(m in lowered for m in markers):
        return _stable_failure(text or "Action failed.")
    return _stable_success(text)


def _stable_learn_action(action_name):
    try:
        remember(f"action_use_{action_name}", str(time.time()))
    except Exception:
        pass


def _stable_open_app(app_name):
    app = (app_name or "").strip().lower()
    if not app:
        return _stable_failure("Which app should I open?")
    target = APP_PATHS_STABLE.get(app)
    if target is None:
        target = app if app.endswith(".exe") else f"{app}.exe"
    try:
        os.startfile(target)
        time.sleep(1.5)
        log_activity("open_app", app)
        _stable_learn_action("open_app")
        return _stable_success(f"Opening {app}.")
    except Exception as exc:
        print(f"[DEBUG][executor] open_app error: {exc}")
        return _stable_failure(f"Couldn't open {app}.")


def _stable_close_app(app_name):
    app = (app_name or "").strip().lower()
    if not app:
        return _stable_failure("Which app should I close?")
    aliases = PROCESS_ALIASES_STABLE.get(app, {app, f"{app}.exe"})
    killed = False
    try:
        for proc in psutil.process_iter(attrs=["name", "pid"]):
            proc_name = (proc.info.get("name") or "").lower()
            if proc_name in aliases or app in proc_name:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        continue
                killed = True
        if killed:
            log_activity("close_app", app)
            _stable_learn_action("close_app")
            return _stable_success(f"Closed {app}.")
        return _stable_failure(f"{app} is not running.")
    except Exception as exc:
        print(f"[DEBUG][executor] close_app error: {exc}")
        return _stable_failure(f"Couldn't close {app}.")


def _stable_join_meeting(link, subject="class"):
    if not link:
        return _stable_failure("No meeting link found.")
    try:
        webbrowser.open(link)
        time.sleep(5)
        _require_pyautogui().hotkey("ctrl", "d")
        time.sleep(0.4)
        _require_pyautogui().hotkey("ctrl", "e")
        time.sleep(0.8)
        for _ in range(8):
            _require_pyautogui().press("tab")
            time.sleep(0.2)
        _require_pyautogui().press("enter")
        log_activity("join_meeting", f"{subject}: {link[:60]}")
        _stable_learn_action("join_meeting")
        return _stable_success(f"Joined the meeting for {subject}.")
    except Exception as exc:
        print(f"[DEBUG][executor] join_meeting error: {exc}")
        try:
            webbrowser.open(link)
        except Exception:
            pass
        return _stable_failure("I opened the meeting link, but the join step failed.")


def _stable_whatsapp_read():
    try:
        from whatsapp_fetcher import extract_whatsapp_text, open_group, open_whatsapp
        import core
        import brain

        open_whatsapp()
        time.sleep(1.5)
        open_group("24 BATCH 3")
        time.sleep(3)
        _require_pyautogui().scroll(-300)
        time.sleep(0.8)

        raw_text = extract_whatsapp_text()
        if not raw_text or "No readable" in raw_text:
            return _stable_success("No updates found in WhatsApp.")

        events = core.parse_whatsapp_text(raw_text)
        action, response = core.format_whatsapp_response(events)

        if not action and not response:
            classified = brain.analyze(raw_text)
            msg_type   = classified.get("type", "ignore")
            summary    = classified.get("summary", "")
            if msg_type == "important":
                response = summary or "There's an important update."
            elif msg_type == "notification":
                response = summary or "Got some updates."
            else:
                response = "Nothing important."

        if response and "timetable" in (response or "").lower():
            remember("latest_timetable", raw_text[:2000])
        if action and action.get("action") == "join_meeting":
            remember("latest_class_detected", json.dumps(action))

        log_activity("whatsapp_read", raw_text[:120])
        return _stable_success(response or "No important updates found.")
    except Exception as exc:
        print(f"[DEBUG][executor] whatsapp_read error: {exc}")
        return _stable_failure("Something went wrong.")


def _stable_update_timetable(group_name):
    try:
        from whatsapp_fetcher import update_timetable

        success = update_timetable(group_name or "24 BATCH 3")
        if success:
            return _stable_success("Got it.")
        return _stable_failure("Couldn't fetch it.")
    except Exception as exc:
        print(f"[DEBUG][executor] whatsapp_download error: {exc}")
        return _stable_failure("Something went wrong.")


IMAGE_GEN_API_KEY = ""
VIDEO_GEN_API_KEY = ""


def generate_image(prompt: str, style: str = "realistic") -> str:
    return "Image generation is not configured yet."


# ── Video output folder ────────────────────────────────────────────────────────
_VIDEO_OUTPUT_DIR = os.path.join(BASE_DIR, "generated_media", "videos")

# ── Video prompt cleaner ───────────────────────────────────────────────────────
_VIDEO_TRIGGER_PHRASES = (
    "generate a video of",
    "generate a video showing",
    "generate a video about",
    "generate video of",
    "generate video showing",
    "generate video about",
    "make a video of",
    "make a video showing",
    "make a video about",
    "make video of",
    "create a video of",
    "create a video showing",
    "create a video about",
    "create video of",
    "produce a video of",
    "produce a video showing",
    "produce video of",
    "generate a short video of",
    "generate a short video showing",
    "generate a short video about",
    "make a short video of",
    "make a short video showing",
    "make a short video about",
)

def _clean_video_prompt(raw_prompt: str) -> str:
    """
    Strip voice-trigger preamble from the raw user utterance so the model
    receives only the descriptive content.
    """
    lowered = (raw_prompt or "").strip().lower()
    for phrase in sorted(_VIDEO_TRIGGER_PHRASES, key=len, reverse=True):
        if lowered.startswith(phrase):
            cleaned = raw_prompt.strip()[len(phrase):].strip()
            cleaned = re.sub(r"^(a |an )", "", cleaned, flags=re.IGNORECASE).strip()
            return cleaned if cleaned else raw_prompt.strip()
    return raw_prompt.strip()


def generate_video(prompt: str, duration: int = 5) -> str:
    """Experimental video generation; disabled unless explicitly enabled."""
    if os.getenv("JARVIS_ENABLE_EXPERIMENTAL_VIDEO_GENERATION", "false").lower() != "true":
        return "Video generation is experimental and disabled in this MARK VII release."
    if not API_KEY:
        return "OpenRouter API key is missing. Add OPENROUTER_API_KEY to your .env file."

    clean_prompt = _clean_video_prompt(prompt)
    if not clean_prompt:
        return "I need a description for the video. Try: generate a video of a sunset."

    duration = max(5, min(int(duration), 10))

    print(f"[DEBUG][executor] generate_video prompt='{clean_prompt}' duration={duration}s")

    os.makedirs(_VIDEO_OUTPUT_DIR, exist_ok=True)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type":  "application/json",
    }

    payload = {
        "model": "kwaivgi/kling-v3.0-std",
        "messages": [
            {
                "role":    "user",
                "content": clean_prompt,
            }
        ],
        "duration":         duration,
        "aspect_ratio":     "16:9",
        "cfg_scale":        0.5,
    }

    if _speak_fn:
        _speak_fn("On it. Video generation can take up to a minute, hang tight.")

    try:
        resp = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.exceptions.Timeout:
        print("[DEBUG][executor] generate_video timed out")
        return (
            "The video request timed out. The model might be busy — "
            "try again in a moment."
        )
    except requests.exceptions.RequestException as exc:
        print(f"[DEBUG][executor] generate_video network error: {exc}")
        return "Couldn't reach the video generation service. Check your connection."

    try:
        data = resp.json()
    except ValueError:
        print(f"[DEBUG][executor] generate_video bad JSON, status={resp.status_code}")
        return "Got an unexpected response from the video service."

    print(f"[DEBUG][executor] generate_video raw response: {json.dumps(data)[:400]}")

    status_code = resp.status_code
    if status_code == 429:
        return (
            "The video model is rate-limited right now. "
            "Try again in a minute or two."
        )
    if status_code == 202:
        return (
            "The video is queued and being generated. "
            "This model sometimes takes longer — I'll let you know when it's ready."
        )
    if status_code not in (200, 201):
        err_msg = data.get("error", {}).get("message", "") if isinstance(data, dict) else ""
        print(f"[DEBUG][executor] generate_video API error {status_code}: {err_msg}")
        return (
            f"Video generation failed (status {status_code}). "
            + (err_msg[:120] if err_msg else "The model returned an error.")
        )

    video_url  = None
    video_b64  = None

    choices = data.get("choices", [])
    if choices and isinstance(choices, list):
        first   = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {})
        content = message.get("content", "")

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "video_url":
                    video_url = (block.get("video_url") or {}).get("url", "")
                    break
                if btype == "text":
                    text_val = block.get("text", "")
                    url_match = re.search(r"https?://\S+\.mp4\S*", text_val)
                    if url_match:
                        video_url = url_match.group(0)
                        break
        elif isinstance(content, str):
            url_match = re.search(r"https?://\S+\.mp4\S*", content)
            if url_match:
                video_url = url_match.group(0)

    if not video_url:
        video_url = data.get("video_url") or data.get("url") or ""

    if not video_url and not video_b64:
        print(f"[DEBUG][executor] generate_video no media found in: {json.dumps(data)[:600]}")
        return (
            "The video model responded but I couldn't find the video in its reply. "
            "It may still be processing — try asking again in a moment."
        )

    timestamp  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename   = f"video_{timestamp}.mp4"
    save_path  = os.path.join(_VIDEO_OUTPUT_DIR, filename)

    try:
        if video_url:
            print(f"[DEBUG][executor] generate_video downloading from {video_url[:80]}")
            video_resp = requests.get(video_url, timeout=60, stream=True)
            video_resp.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        elif video_b64:
            import base64
            with open(save_path, "wb") as f:
                f.write(base64.b64decode(video_b64))

        log_activity("generate_video", clean_prompt[:80])
        print(f"[DEBUG][executor] generate_video saved to {save_path}")

        try:
            os.startfile(save_path)
        except Exception as open_exc:
            print(f"[DEBUG][executor] generate_video couldn't auto-open: {open_exc}")

        return (
            f"Done. Your video has been saved to generated_media/videos/{filename} "
            f"and opened for you."
        )

    except requests.exceptions.RequestException as dl_exc:
        print(f"[DEBUG][executor] generate_video download error: {dl_exc}")
        return (
            "The video was generated but I couldn't download it. "
            f"Try grabbing it manually: {video_url}"
        )
    except Exception as save_exc:
        print(f"[DEBUG][executor] generate_video save error: {save_exc}")
        return "The video was generated but something went wrong while saving it."


# ── Obligation action handlers (Layer 1) ──────────────────────────────────────

def _handle_add_obligation(action: dict) -> tuple:
    try:
        import obligations
        ob = obligations.add_obligation(
            title    = action.get("title", "Unnamed obligation"),
            ob_type  = action.get("type", "other"),
            due_date = action.get("due_date", ""),
            status   = action.get("status", "pending"),
            source   = action.get("source", "voice"),
            notes    = action.get("notes", ""),
        )
        due_label = ""
        if ob.get("due_date"):
            try:
                import datetime as _dt
                due_dt    = _dt.datetime.fromisoformat(ob["due_date"])
                due_label = f", due {due_dt.strftime('%a %d %b')}"
            except Exception:
                due_label = f", due {ob['due_date']}"
        log_activity("add_obligation", ob.get("title", ""))
        return _stable_success(
            f"Logged — {ob['title']} ({ob['type']}){due_label}. ID: {ob['id']}."
        )
    except Exception as exc:
        print(f"[DEBUG][executor] add_obligation error: {exc}")
        return _stable_failure("Couldn't save that obligation.")


def _handle_query_obligations(action: dict) -> tuple:
    try:
        import obligations
        mode = action.get("mode", "pending")

        if mode == "overdue":
            obs   = obligations.get_overdue()
            label = "overdue"
        elif mode == "due_soon":
            obs   = obligations.get_due_soon(days=3)
            label = "due in the next 3 days"
        else:
            obs   = obligations.get_pending()
            label = "pending"

        if not obs:
            return _stable_success(f"Nothing {label} right now.")

        lines = []
        for i, ob in enumerate(obs, 1):
            lines.append(obligations.format_obligation(ob))
        result = f"{len(obs)} {label}: " + ". ".join(lines)
        return _stable_success(result)
    except Exception as exc:
        print(f"[DEBUG][executor] query_obligations error: {exc}")
        return _stable_failure("Couldn't fetch obligations.")


def _handle_portal_scan(action: dict, speak_fn=None) -> tuple:
    try:
        import portal_scan
        if speak_fn:
            speak_fn("Give me a moment to read your screen.")
        result   = portal_scan.scan_portal_screen()
        proposal = portal_scan.format_scan_proposal(result)
        _pending_portal_proposals.clear()
        _pending_portal_proposals.extend(result.get("proposed", []))
        log_activity("portal_scan", f"{len(_pending_portal_proposals)} items found")
        return _stable_success(proposal)
    except Exception as exc:
        print(f"[DEBUG][executor] portal_scan error: {exc}")
        return _stable_failure("Couldn't scan the screen.")


def _handle_mark_obligation_done(action: dict) -> tuple:
    try:
        import obligations
        title_hint = (action.get("title_hint", "") or "").lower().strip()
        if not title_hint:
            return _stable_failure("Which obligation should I mark done?")

        all_obs = obligations.get_all()
        active = [ob for ob in all_obs if ob.get("status") not in ("done",)]
        if not active:
            return _stable_success("No active obligations to mark done.")

        hint_words = set(title_hint.split())
        best_match = None
        best_score = 0

        for ob in active:
            ob_title_words = set(ob.get("title", "").lower().split())
            overlap = len(hint_words & ob_title_words)
            if overlap > best_score:
                best_score = overlap
                best_match = ob

        if not best_match or best_score == 0:
            return _stable_failure(
                f"I couldn't find an obligation matching '{title_hint}'. "
                "Try 'what's pending' to see the full list."
            )

        obligations.mark_done(best_match["id"])
        log_activity("mark_obligation_done", best_match.get("title", ""))
        return _stable_success(f"Done — marked '{best_match['title']}' as complete.")
    except Exception as exc:
        print(f"[DEBUG][executor] mark_obligation_done error: {exc}")
        return _stable_failure("Couldn't update that obligation.")


# ── Pending portal proposals ──────────────────────────────────────────────────
_pending_portal_proposals: list = []


def get_pending_portal_proposals() -> list:
    return list(_pending_portal_proposals)


def confirm_portal_proposals() -> str:
    if not _pending_portal_proposals:
        return "No pending proposals to add."
    try:
        import obligations
        added = []
        for proposal in _pending_portal_proposals:
            ob = obligations.add_obligation(
                title    = proposal.get("title", "Unknown"),
                ob_type  = proposal.get("type", "other"),
                due_date = proposal.get("due_date", ""),
                source   = "portal_scan",
            )
            added.append(ob.get("title", ""))
        _pending_portal_proposals.clear()
        log_activity("portal_scan_confirmed", f"{len(added)} added")
        if len(added) == 1:
            return f"Added {added[0]} to your obligations."
        return f"Added {len(added)} obligations: {', '.join(added)}."
    except Exception as exc:
        print(f"[DEBUG][executor] confirm_portal_proposals error: {exc}")
        return "Something went wrong adding the obligations."


# ══════════════════════════════════════════════════════════════════════════════
# NEW: IRON MAN AUTOMATION ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _make_speech_progress_callback(app_name: str):
    """
    Create a progress callback that speaks intelligent updates as install/login
    events happen. Uses smart throttling — doesn't spam the user with every event.
    """
    state = {
        "last_spoken":     "",
        "last_download":   -1,
        "install_started": False,
    }

    def _cb(event: str, data: dict):
        if _speak_fn is None:
            return

        try:
            # ── Install events ─────────────────────────────────────────────
            if event == "resolving":
                pass  # too fast to bother speaking

            elif event == "starting":
                if not state["install_started"]:
                    state["install_started"] = True
                    msg = f"Downloading {app_name} now — takes a minute."
                    _speak_fn(msg)
                    state["last_spoken"] = msg

            elif event == "downloading":
                percent = data.get("percent", 0)
                # Only speak at 50% (once) so we don't spam
                if percent >= 50 and state["last_download"] < 50:
                    state["last_download"] = percent
                    _speak_fn("Halfway there.")

            elif event == "installing":
                if "installing" not in state["last_spoken"]:
                    _speak_fn("Downloaded, installing now.")
                    state["last_spoken"] = "installing"

            elif event == "success":
                _speak_fn(f"{app_name} installed.")
                state["last_spoken"] = "installed"

            elif event == "already":
                _speak_fn(f"{app_name} is already installed.")

            elif event == "failed":
                reason = data.get("reason", "Unknown error")
                _speak_fn(f"Install failed. {reason}")

            # ── Launch + login events ──────────────────────────────────────
            elif event == "launching":
                app = data.get("app", app_name)
                _speak_fn(f"Opening {app}.")

            elif event == "waiting_for_window":
                pass  # silent

            elif event == "window_found":
                pass  # silent, we'll speak about login next

            elif event == "no_login_needed":
                _speak_fn(f"{app_name} is open. Ready when you are.")

            elif event == "checking_credentials":
                pass  # silent

            elif event == "credentials_loaded":
                _speak_fn(f"I see the login screen. Signing you in.")

            elif event == "no_credentials":
                _speak_fn(
                    f"{app_name} is open, but I don't have your login saved. "
                    f"Say 'save my {app_name} login' to set it up."
                )

            elif event == "logging_in":
                pass  # already announced

            elif event == "login_step":
                pass  # too granular

            elif event == "login_step_failed":
                reason = data.get("reason", "")
                _speak_fn(f"Login step failed. {reason}")

            elif event == "verifying_login":
                pass  # silent

            elif event == "login_success":
                app = data.get("app", app_name)
                _speak_fn(f"You're logged into {app}.")

            elif event == "login_unverified":
                reason = data.get("reason", "")
                _speak_fn(
                    f"I tried logging in but couldn't confirm it worked. "
                    f"Check the window and log in manually if needed."
                )

            elif event == "launch_failed":
                reason = data.get("reason", "")
                _speak_fn(f"Couldn't open {app_name}. {reason}")

            elif event == "window_never_appeared":
                _speak_fn(f"{app_name} didn't open in time. Something may be wrong.")

        except Exception as cb_exc:
            print(f"[DEBUG][executor] progress callback error: {cb_exc}")

    return _cb


def _handle_install_app(action: dict) -> tuple:
    """Install an app via winget with live speech progress."""
    try:
        import login_orchestrator
        import auto_login_profiles

        app_name = (action.get("app", "") or action.get("query", "")).strip()
        if not app_name:
            return _stable_failure("Which app should I install?")

        display_name = app_name
        profile = auto_login_profiles.get_profile(app_name)
        if profile:
            display_name = profile.get("display_name", app_name)

        callback = _make_speech_progress_callback(display_name)
        result = login_orchestrator.install_only(app_name, progress_callback=callback)

        if result.get("success"):
            log_activity("install_app", display_name)
            _stable_learn_action("install_app")
            return _stable_success("")
        else:
            return _stable_failure(result.get("message", "Install failed."))

    except Exception as exc:
        print(f"[DEBUG][executor] install_app error: {exc}")
        return _stable_failure(f"Something went wrong: {exc}")


def _handle_install_and_login(action: dict) -> tuple:
    """Install + launch + auto-login in one flow."""
    try:
        import login_orchestrator
        import auto_login_profiles

        app_name = (action.get("app", "") or action.get("query", "")).strip()
        if not app_name:
            return _stable_failure("Which app should I install and log into?")

        display_name = app_name
        profile = auto_login_profiles.get_profile(app_name)
        if profile:
            display_name = profile.get("display_name", app_name)

        callback = _make_speech_progress_callback(display_name)
        result = login_orchestrator.install_and_setup(app_name, progress_callback=callback)

        if result.get("success"):
            log_activity("install_and_login", display_name)
            _stable_learn_action("install_and_login")
            return _stable_success("")
        else:
            return _stable_failure(result.get("message", "Setup failed."))

    except Exception as exc:
        print(f"[DEBUG][executor] install_and_login error: {exc}")
        return _stable_failure(f"Something went wrong: {exc}")


def _handle_open_and_login(action: dict) -> tuple:
    """Launch already-installed app and auto-login."""
    try:
        import login_orchestrator
        import auto_login_profiles

        app_name = (action.get("app", "") or "").strip()
        if not app_name:
            return _stable_failure("Which app?")

        display_name = app_name
        profile = auto_login_profiles.get_profile(app_name)
        if profile:
            display_name = profile.get("display_name", app_name)

        callback = _make_speech_progress_callback(display_name)
        result = login_orchestrator.launch_and_login(app_name, progress_callback=callback)

        if result.get("success"):
            log_activity("open_and_login", display_name)
            _stable_learn_action("open_and_login")
            return _stable_success("")
        else:
            return _stable_failure(result.get("message", "Couldn't complete."))

    except Exception as exc:
        print(f"[DEBUG][executor] open_and_login error: {exc}")
        return _stable_failure(f"Something went wrong: {exc}")


def _handle_save_login(action: dict) -> tuple:
    """
    Save credentials for an app.
    Expects action to have 'app', 'username', 'password' already collected
    by the jarvis.py dialog flow.
    """
    try:
        import credential_vault

        app_name = (action.get("app", "") or "").strip()
        username = (action.get("username", "") or "").strip()
        password = action.get("password", "") or ""

        if not app_name:
            return _stable_failure("Which app is this for?")
        if not username:
            return _stable_failure("I need a username or email.")
        if not password:
            return _stable_failure("I need a password.")

        result = credential_vault.save_credential(app_name, username, password)
        if result["success"]:
            log_activity("save_login", app_name)
            return _stable_success(
                f"Saved. I'll use it next time you open {app_name}."
            )
        else:
            return _stable_failure(result["message"])

    except Exception as exc:
        print(f"[DEBUG][executor] save_login error: {exc}")
        return _stable_failure(f"Couldn't save that: {exc}")


def _handle_list_logins(action: dict) -> tuple:
    """List all saved app credentials."""
    try:
        import credential_vault

        apps = credential_vault.list_saved_apps()
        if not apps:
            return _stable_success("You haven't saved any app logins yet.")

        pretty = ", ".join(app.replace("_", " ").title() for app in apps)
        return _stable_success(f"I have login credentials saved for: {pretty}.")

    except Exception as exc:
        print(f"[DEBUG][executor] list_logins error: {exc}")
        return _stable_failure("Couldn't check saved logins.")


def _handle_delete_login(action: dict) -> tuple:
    """Delete saved credentials for an app."""
    try:
        import credential_vault

        app_name = (action.get("app", "") or "").strip()
        if not app_name:
            return _stable_failure("Which app's login should I forget?")

        result = credential_vault.delete_credential(app_name)
        if result["success"]:
            log_activity("delete_login", app_name)
            return _stable_success(result["message"])
        else:
            return _stable_failure(result["message"])

    except Exception as exc:
        print(f"[DEBUG][executor] delete_login error: {exc}")
        return _stable_failure(f"Couldn't remove that: {exc}")


def _handle_self_scan(action: dict) -> tuple:
    """Force a self-awareness rescan."""
    try:
        import self_awareness
        scan = self_awareness.force_rescan()
        total = scan.get("total_files", 0)
        funcs = scan.get("total_functions", 0)
        changes = scan.get("changes", {})

        summary = f"Scanned myself. {total} files, {funcs} public functions."

        added = changes.get("added", [])
        modified = changes.get("modified", [])
        removed = changes.get("removed", [])

        if added:
            summary += f" I gained {len(added)} new file(s): {', '.join(added)}."
        if modified:
            summary += f" {len(modified)} file(s) were updated."
        if removed:
            summary += f" {len(removed)} file(s) were removed."

        return _stable_success(summary)

    except Exception as exc:
        print(f"[DEBUG][executor] self_scan error: {exc}")
        return _stable_failure("Couldn't scan myself.")


def _handle_self_capabilities(action: dict) -> tuple:
    """Report current capabilities based on file scan."""
    try:
        import self_awareness
        summary = self_awareness.get_ability_summary()
        if not summary:
            return _stable_failure("I haven't scanned my abilities yet.")
        if len(summary) > 800:
            return _stable_success(
                summary[:800] + "... and more. Check the console for the full list."
            )
        return _stable_success(summary)

    except Exception as exc:
        print(f"[DEBUG][executor] self_capabilities error: {exc}")
        return _stable_failure("Couldn't check my abilities.")


def _handle_self_changes(action: dict) -> tuple:
    """Report what changed since last boot."""
    try:
        import self_awareness
        summary = self_awareness.get_change_summary()
        return _stable_success(summary)

    except Exception as exc:
        print(f"[DEBUG][executor] self_changes error: {exc}")
        return _stable_failure("Couldn't check for changes.")


# ══════════════════════════════════════════════════════════════════════════════
# FILE PROCESSOR ACTIONS (MARK VII - File Intelligence)
# ══════════════════════════════════════════════════════════════════════════════

def _handle_process_file(action: dict) -> tuple:
    """Process a file with the universal file processor."""
    try:
        from file_processor import process_file, get_processor
        
        file_path = action.get("file_path", "") or action.get("path", "")
        file_action = action.get("file_action", "inspect")
        
        if not file_path:
            return _stable_failure("Which file should I process? Provide a file path.")
        
        # Run the file processor
        result = process_file(file_path, file_action)
        
        if result.success:
            # Format response based on action type
            if file_action == "inspect":
                meta = result.metadata
                inspect_data = result.result if isinstance(result.result, dict) else {}
                size_human = inspect_data.get("size_human", f"{meta.size} bytes")
                response = (
                    f"File: {meta.name} ({size_human})\n"
                    f"Type: {meta.mime_type}\n"
                    f"SHA256: {meta.sha256[:16]}...\n"
                    f"Modified: {meta.modified.strftime('%Y-%m-%d %H:%M')}"
                )
                if meta.extra:
                    for k, v in meta.extra.items():
                        response += f"\n  {k}: {v}"
                return _stable_success(response)
            
            elif file_action == "summarize":
                return _stable_success(result.result)
            
            elif file_action == "explain":
                return _stable_success(result.result)
            
            elif file_action == "extract":
                text_data = result.result
                preview = text_data.get("text", "")[:500]
                return _stable_success(
                    f"Extracted {text_data.get('word_count', 0)} words, "
                    f"{text_data.get('line_count', 0)} lines:\n{preview}"
                )
            
            elif file_action == "ocr":
                return _stable_success(f"OCR result:\n{result.result}")
            
            elif file_action == "transcribe":
                return _stable_success(f"Transcription:\n{result.result}")
            
            elif file_action == "analyze":
                if isinstance(result.result, dict) and "error" not in result.result:
                    data = result.result
                    if "shape" in data:
                        return _stable_success(
                            f"CSV Analysis: {data['shape'][0]} rows × {data['shape'][1]} cols\n"
                            f"Columns: {', '.join(data['columns'][:5])}{'...' if len(data['columns']) > 5 else ''}\n"
                            f"Memory: {data.get('memory_mb', 0):.1f} MB"
                        )
                    elif "keys" in data:
                        return _stable_success(
                            f"JSON Analysis: {data['type']} with {data.get('length', 0)} items\n"
                            f"Keys: {', '.join(data['keys'][:5]) if data['keys'] else 'N/A'}"
                        )
                return _stable_success(str(result.result))
            
            elif file_action == "code_review":
                return _stable_success(result.result)
            
            elif file_action == "debug":
                return _stable_success(result.result)
            
            elif file_action == "convert":
                if isinstance(result.result, dict) and result.result.get("success"):
                    return _stable_success(f"Converted to {result.result['output']}")
                return _stable_failure(result.result.get("error", "Conversion failed"))
            
            else:
                return _stable_success(str(result.result))
        else:
            return _stable_failure(result.error or "File processing failed")
            
    except Exception as exc:
        print(f"[DEBUG][executor] process_file error: {exc}")
        return _stable_failure(f"Couldn't process file: {exc}")


def _handle_list_uploaded_files(action: dict) -> tuple:
    """List files in the upload directory (for server mode)."""
    try:
        import os
        from server import UPLOAD_FOLDER
        
        if not os.path.exists(UPLOAD_FOLDER):
            return _stable_success("No uploaded files.")
        
        files = []
        for fname in os.listdir(UPLOAD_FOLDER):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath):
                size = os.path.getsize(fpath) / 1024
                files.append(f"{fname} ({size:.1f} KB)")
        
        if not files:
            return _stable_success("No uploaded files.")
        
        return _stable_success("Uploaded files:\n" + "\n".join(files[:20]))
    except Exception as exc:
        print(f"[DEBUG][executor] list_uploaded_files error: {exc}")
        return _stable_failure("Couldn't list files.")


# ══════════════════════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════════════════════
# VISION/EYES ACTIONS (MARK VII - Visual Awareness)
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_capture_webcam(action: dict) -> tuple:
    """Capture a frame from the webcam and optionally describe it."""
    try:
        from vision import capture_webcam, describe_image, get_vision_status
        
        camera_index = action.get("camera_index", 0)
        describe = action.get("describe", True)
        prompt = action.get("prompt", "describe what you see in this webcam capture")
        
        frame = capture_webcam(camera_index)
        if frame is None:
            return _stable_failure("Could not access webcam. Make sure it's connected and not in use by another app.")
        
        if describe:
            result = describe_image(frame, prompt)
            return _stable_success(result)
        else:
            return _stable_success("Webcam frame captured successfully.")
            
    except Exception as exc:
        print(f"[DEBUG][executor] capture_webcam error: {exc}")
        return _stable_failure(f"Webcam capture failed: {exc}")


def _handle_capture_screen_region(action: dict) -> tuple:
    """Capture a specific screen region."""
    try:
        from vision import capture_screen_region, describe_image
        import cv2
        
        left = action.get("left", 0)
        top = action.get("top", 0)
        width = action.get("width", 800)
        height = action.get("height", 600)
        describe = action.get("describe", True)
        prompt = action.get("prompt", "describe what you see in this screen region")
        save_path = action.get("save_path", "")
        
        img = capture_screen_region(left, top, width, height)
        
        # Save if requested
        if save_path:
            cv2.imwrite(save_path, img)
            saved_msg = " Saved to " + save_path + "."
        else:
            saved_msg = ""
        
        if describe:
            result = describe_image(img, prompt)
            return _stable_success(result + saved_msg)
        else:
            return _stable_success("Screen region captured (" + str(width) + "x" + str(height) + " at " + str(left) + "," + str(top) + ")" + saved_msg)
            
    except Exception as exc:
        print(f"[DEBUG][executor] capture_screen_region error: {exc}")
        return _stable_failure("Screen region capture failed: " + str(exc))


def _handle_analyze_image_file(action: dict) -> tuple:
    """Analyze an image file using Gemini Vision."""
    try:
        from vision import analyze_image_file
        
        file_path = action.get("file_path", "") or action.get("path", "")
        prompt = action.get("prompt", "describe what you see in this image")
        
        if not file_path:
            return _stable_failure("Which image file should I analyze? Provide a file path.")
        
        result = analyze_image_file(file_path, prompt)
        return _stable_success(result)
        
    except Exception as exc:
        print(f"[DEBUG][executor] analyze_image_file error: {exc}")
        return _stable_failure("Image analysis failed: " + str(exc))


def _handle_read_image_text(action: dict) -> tuple:
    """Extract text from an image file using OCR."""
    try:
        from vision import read_image_text
        
        file_path = action.get("file_path", "") or action.get("path", "")
        
        if not file_path:
            return _stable_failure("Which image file should I read? Provide a file path.")
        
        result = read_image_text(file_path)
        if result.startswith(("OCR unavailable:", "OCR failed:", "OCR timed out.",
                              "No text detected", "File not found:", "Could not read image")):
            return _stable_failure(result)
        return _stable_success(result)
        
    except Exception as exc:
        print(f"[DEBUG][executor] read_image_text error: {exc}")
        return _stable_failure("Image OCR failed: " + str(exc))


def _handle_vision_status(action: dict) -> tuple:
    """Get status of all vision subsystems."""
    try:
        from vision import get_vision_status
        
        status = get_vision_status()
        lines = []
        for subsystem, info in status.items():
            state = info.get("state", "unknown")
            detail = info.get("detail", "")
            lines.append(subsystem + ": " + state + " -- " + detail)
        
        return _stable_success("Vision subsystem status:\n" + "\n".join(lines))
        
    except Exception as exc:
        print(f"[DEBUG][executor] vision_status error: {exc}")
        return _stable_failure("Couldn't get vision status: " + str(exc))


# MAIN EXECUTE
# ══════════════════════════════════════════════════════════════════════════════

@runtime_visuals.visual_activity("executing", "current_action")
def execute(action, speak_fn=None):
    global _pending_confirm
    if not action:
        return _stable_failure("No action to execute.")

    update_daily_stats("actions_executed")
    act = action.get("action", "")
    print(f"[DEBUG][executor] intent detected: {act}")

    try:
        if act in CONFIRM_ACTIONS:
            now = time.monotonic()
            if (isinstance(_pending_confirm, tuple) and _pending_confirm[0] == act
                    and now - _pending_confirm[1] <= _CONFIRM_TTL):
                _pending_confirm = None
            else:
                _pending_confirm = (act, now)
                return _stable_failure(
                    f"Say '{act.replace('_', ' ')}' again within {int(_CONFIRM_TTL)} seconds to confirm."
                )

        if act == "open_app":
            return _stable_open_app(action.get("app", ""))
        if act == "close_app":
            return _stable_close_app(action.get("app", ""))
        if act == "join_meeting":
            return _stable_join_meeting(action.get("link", ""), action.get("subject", "class"))
        if act == "media":
            _require_pyautogui().press(action.get("key", "space"))
            _stable_learn_action("media")
            return _stable_success("")
        if act == "scroll":
            _require_pyautogui().scroll(500 if action.get("direction") == "up" else -500)
            _stable_learn_action("scroll")
            return _stable_success("")
        if act == "click":
            _require_pyautogui().click()
            _stable_learn_action("click")
            return _stable_success("")
        if act in {"type_text", "voice_type"}:
            _require_pyautogui().write(action.get("text", ""), interval=0.04)
            log_activity(act, action.get("text", "")[:40])
            _stable_learn_action(act)
            return _stable_success("")
        if act == "read_screen":
            try:
                text = read_screen_text()
                if not text:
                    return _stable_failure("Couldn't read the screen.")
                return _stable_success(text[:500])
            except Exception as exc:
                print(f"[DEBUG][executor] read_screen error: {exc}")
                return _stable_failure("Couldn't read the screen.")
        if act == "screenshot_describe":
            try:
                if speak_fn:
                    speak_fn("Analyzing screen...")
                from vision import describe_screen
                result = describe_screen(
                    "describe what you see on this screen in a natural way"
                )
                return _stable_success(result)
            except Exception as exc:
                print(f"[DEBUG][executor] screenshot_describe error: {exc}")
                return _stable_failure("Couldn't analyze the screen.")
        if act == "system_info":
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            battery = psutil.sensors_battery()
            battery_info = (
                f"{battery.percent:.0f}% {'charging' if battery.power_plugged else 'on battery'}"
                if battery
                else "unknown"
            )
            return _stable_success(f"CPU at {cpu}%. RAM {ram.percent}% used. Battery {battery_info}.")
        if act == "system_status":
            try:
                from system_monitor import get_system_status
                mode = action.get("mode", "full")
                return _stable_success(get_system_status(mode=mode))
            except Exception as exc:
                print(f"[DEBUG][executor] system_status error: {exc}")
                return _stable_failure("Couldn't read system status.")
        if act == "datetime":
            now = datetime.datetime.now()
            return _stable_success(f"It's {now.strftime('%I:%M %p')}, {now.strftime('%A %d %B %Y')}.")
        if act == "web_search":
            try:
                return _stable_from_text(
                    web_search(action.get("query", "")),
                    ("unavailable", "couldn't find", "search failed"),
                )
            except Exception as exc:
                print(f"[DEBUG][executor] web_search error: {exc}")
                return _stable_failure("Search failed.")
        if act == "summarize_url":
            url = action.get("url", "")
            try:
                if "youtube" in url or "youtu.be" in url:
                    result = summarize_youtube(url)
                else:
                    result = summarize_article(url)
                return _stable_from_text(
                    result,
                    ("doesn't look", "no accessible transcript", "couldn't retrieve",
                     "summarization is unavailable", "only summarize public",
                     "invalid redirect", "redirected too many", "doesn't appear",
                     "couldn't extract", "trouble reading"),
                )
            except Exception as exc:
                print(f"[DEBUG][executor] summarize_url error: {exc}")
                return _stable_failure("Couldn't summarize that.")
        if act == "remember":
            remember(action.get("key", ""), action.get("value", ""))
            return _stable_success("Saved.")
        if act == "recall":
            return _stable_success(recall_all())
        if act == "weather":
            city = action.get("city", "your city")
            return _stable_success(get_weather(city))
        if act == "news":
            return _stable_success(get_news())
        if act == "set_reminder":
            from tasks import add_reminder
            try:
                result = add_reminder(action.get("message", "something"), int(action.get("seconds", 3600)))
                return _stable_success(result)
            except Exception as exc:
                print(f"[DEBUG][executor] set_reminder error: {exc}")
                return _stable_failure("Couldn't set the reminder.")
        if act == "list_reminders":
            from tasks import list_reminders
            return _stable_success(list_reminders())
        if act == "lock_pc":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
            return _stable_success("Locking it.")
        if act == "shutdown_pc":
            subprocess.run(["shutdown", "/s", "/t", "5"], check=False)
            return _stable_success("Shutting down in 5.")
        if act == "restart_pc":
            subprocess.run(["shutdown", "/r", "/t", "5"], check=False)
            return _stable_success("Restarting in 5.")
        if act == "clipboard_read":
            result = clipboard_read()
            if result is False:
                return _stable_failure("Couldn't read the clipboard.")
            return _stable_success(result)
        if act == "clipboard_write":
            result = clipboard_write(action.get("text", ""))
            if result is False:
                return _stable_failure("Couldn't copy that.")
            return _stable_success(result)
        if act == "open_file":
            return _stable_success(open_file(action.get("name", "")))
        if act == "list_folder":
            return _stable_success(list_folder(action.get("folder", "Downloads")))
        if act == "search_file":
            query = action.get("query", "") or action.get("name", "")
            if not query:
                return _stable_failure("What file should I search for?")
            search_files, _ = _get_file_ops()
            return _stable_success(search_files(query))
        if act == "rename_file":
            old = action.get("old_name", "") or action.get("name", "")
            new = action.get("new_name", "")
            if not old or not new:
                return _stable_failure("I need both the old and new name to rename a file.")
            _, _rename_file = _get_file_ops()
            return _stable_success(_rename_file(old, new))
        if act == "whatsapp_read":
            return _stable_whatsapp_read()
        if act == "whatsapp_download":
            return _stable_update_timetable(action.get("group", "24 BATCH 3"))
        if act == "whatsapp_timetable_update":
            try:
                from whatsapp_fetcher import extract_timetable_for_batch, extract_whatsapp_text

                text = extract_whatsapp_text()
                if not text:
                    return _stable_failure("Couldn't read timetable messages.")
                batch_name = action.get("batch_name", "24Batch3")
                timetable_text = extract_timetable_for_batch(text, batch_name)
                if not timetable_text:
                    return _stable_failure("Couldn't find that batch timetable.")
                remember(f"timetable_{batch_name}", timetable_text)
                return _stable_success("Timetable updated.")
            except Exception as exc:
                print(f"[DEBUG][executor] whatsapp_timetable_update error: {exc}")
                return _stable_failure("Something went wrong.")
        if act == "run_sequence":
            for step in action.get("steps", []):
                if isinstance(step, dict):
                    success, message = execute(step, speak_fn)
                    if not success:
                        return _stable_failure(message or "Sequence failed.")
                    time.sleep(0.4)
            return _stable_success("Done.")
        if act == "morning_briefing":
            return _stable_success(morning_briefing())

        # ── Safe Gated Reconnected Actions (Phase 4 Re-wired) ──────────────────
        if act == "send_whatsapp":
            res = send_whatsapp(action.get("phone", ""), action.get("message", ""))
            return _stable_from_text(res, ("unavailable", "not installed", "failed", "couldn't", "not configured"))
        if act == "send_email":
            res = send_email(action.get("to", ""), action.get("subject", ""), action.get("body", ""))
            return _stable_from_text(res, ("not set up", "failed", "couldn't", "check smtp"))
        if act == "spotify_play":
            res = spotify_play(action.get("query", action.get("song", "")))
            return _stable_from_text(res, ("not configured", "couldn't", "trouble", "no active", "failed"))
        if act == "spotify_control":
            res = spotify_control(action.get("command", action.get("cmd", "pause")))
            return _stable_from_text(res, ("not connected", "invalid", "failed", "nothing is currently playing"))
        if act == "calendar_today":
            res = calendar_today()
            return _stable_from_text(res, ("not connected", "couldn't fetch"))
        if act == "calendar_add":
            res = calendar_add(action.get("title", ""), action.get("date", ""), action.get("time", "09:00"))
            return _stable_from_text(res, ("not connected", "couldn't add"))

        if act == "exit":
            return _stable_success("")
        if act == "generate_image":
            return _stable_failure(generate_image(
                action.get("prompt", ""), action.get("style", "realistic")
            ))
        if act == "generate_video":
            msg = generate_video(action.get("prompt", ""), int(action.get("duration", 5)))
            if os.getenv("JARVIS_ENABLE_EXPERIMENTAL_VIDEO_GENERATION", "false").lower() != "true":
                return _stable_failure(msg)
            return _stable_from_text(msg, ("failed", "timed out", "couldn't", "missing", "unexpected", "rate-limited"))

        # ── Obligation actions ────────────────────────────────────────────────
        if act == "add_obligation":
            return _handle_add_obligation(action)
        if act == "query_obligations":
            return _handle_query_obligations(action)
        if act == "portal_scan":
            return _handle_portal_scan(action, speak_fn=speak_fn)
        if act == "mark_obligation_done":
            return _handle_mark_obligation_done(action)

        # ── Self-diagnostic ───────────────────────────────────────────────────
        if act == "run_diagnostic":
            try:
                import self_diagnostic
                from session_logger import log_event, save_session

                if _speak_fn:
                    _speak_fn("Running full system checkup. Give me a moment.")

                report = self_diagnostic.run_full_checkup()

                log_event(
                    "diagnostic_report",
                    report,
                    severity="info",
                    module="self_diagnostic",
                    tags=["diagnostic", "health"],
                )
                save_session()

                return _stable_success(report.get("summary", "Checkup complete."))
            except Exception as exc:
                print(f"[DEBUG][executor] run_diagnostic error: {exc}")
                return _stable_failure("Couldn't run the diagnostic. Check the console.")

        # ── User profile actions ──────────────────────────────────────────────
        if act == "profile_query":
            try:
                from user_profile import get_profile_summary
                return _stable_success(get_profile_summary())
            except Exception as exc:
                print(f"[DEBUG][executor] profile_query error: {exc}")
                return _stable_failure("Couldn't read your profile.")

        if act == "profile_forget":
            try:
                from user_profile import forget_entry
                hint = action.get("hint", "")
                return _stable_success(forget_entry(hint))
            except Exception as exc:
                print(f"[DEBUG][executor] profile_forget error: {exc}")
                return _stable_failure("Couldn't remove that entry.")

        if act == "profile_remember":
            try:
                from user_profile import add_single_fact, confirm_and_save_profile
                statement = action.get("statement", "")
                if not statement:
                    return _stable_failure("What should I remember about you?")
                proposed = add_single_fact(statement)
                if not proposed:
                    return _stable_failure("Couldn't extract that. Try rephrasing.")
                result = confirm_and_save_profile(proposed)
                entry_preview = proposed[0].get("value", statement) if proposed else statement
                return _stable_success(f"Got it — saved: '{entry_preview}'.")
            except Exception as exc:
                print(f"[DEBUG][executor] profile_remember error: {exc}")
                return _stable_failure("Couldn't save that.")

        if act == "declarative_fact":
            try:
                from user_profile import add_single_fact, confirm_and_save_profile, is_safe_explicit_fact
                statement = action.get("statement", "")
                if not statement:
                    return _stable_failure("What should I remember?")
                if not is_safe_explicit_fact(statement):
                    return _stable_failure("I won't save that as a profile fact unless you state it explicitly.")
                proposed = add_single_fact(statement)
                if not proposed:
                    return _stable_failure("Couldn't extract that. Try rephrasing.")
                result = confirm_and_save_profile(proposed)
                entry_preview = proposed[0].get("value", statement) if proposed else statement
                return _stable_success(f"Got it — saved: '{entry_preview}'.")
            except Exception as exc:
                print(f"[DEBUG][executor] declarative_fact error: {exc}")
                return _stable_failure("Couldn't save that.")

        if act == "fact_query":
            try:
                from user_profile import find_profile_fact
                fact_name = action.get("fact_name", "")
                if not fact_name:
                    return _stable_failure("What fact would you like to know?")
                match = find_profile_fact(fact_name)
                if match:
                    return _stable_success(match)
                return _stable_failure(f"I don't have a fact about '{fact_name}' saved.")
            except Exception as exc:
                print(f"[DEBUG][executor] fact_query error: {exc}")
                return _stable_failure("Couldn't retrieve that fact.")

        # ── Iron Man automation actions ───────────────────────────────────────
        if act == "install_app":
            return _handle_install_app(action)
        if act == "install_and_login":
            return _handle_install_and_login(action)
        if act == "open_and_login":
            return _handle_open_and_login(action)
        if act == "save_login":
            return _handle_save_login(action)
        if act == "list_logins":
            return _handle_list_logins(action)
        if act == "delete_login":
            return _handle_delete_login(action)
        if act == "self_scan":
            return _handle_self_scan(action)
        if act == "self_capabilities":
            return _handle_self_capabilities(action)
        if act == "self_changes":
            return _handle_self_changes(action)

        # ── File Processor actions (MARK VII) ──────────────────────────────────
        if act == "process_file":
            return _handle_process_file(action)
        if act == "list_uploaded_files":
            return _handle_list_uploaded_files(action)

        
        # Vision/Eyes actions (MARK VII)
        if act == "capture_webcam":
            return _handle_capture_webcam(action)
        if act == "capture_screen_region":
            return _handle_capture_screen_region(action)
        if act == "analyze_image_file":
            return _handle_analyze_image_file(action)
        if act == "read_image_text":
            return _handle_read_image_text(action)
        if act == "vision_status":
            return _handle_vision_status(action)

        
        # Dev Agent actions (MARK VII)
        if _DEV_AGENT_AVAILABLE:
            if act == "dev_inspect":
                return DEV_AGENT_ACTIONS["dev_inspect"](action)
            if act == "dev_test":
                return DEV_AGENT_ACTIONS["dev_test"](action)
            if act == "dev_search":
                return DEV_AGENT_ACTIONS["dev_search"](action)
            if act == "dev_propose":
                return DEV_AGENT_ACTIONS["dev_propose"](action)
            if act == "dev_status":
                return DEV_AGENT_ACTIONS["dev_status"](action)

        return _stable_failure("Unsupported action.")
    except Exception as exc:
        print(f"[DEBUG][executor] unhandled error for action {act}: {exc}")
        return _stable_failure("Something went wrong.")


def execute_with_retry(action, speak_fn=None, max_retries=1):
    action_name = action.get("action", "unknown") if isinstance(action, dict) else "unknown"

    # ── Don't retry install/login flows or generative jobs ─────────────────
    _NO_RETRY_ACTIONS = {
        "install_app", "install_and_login", "open_and_login",
        "save_login", "list_logins", "delete_login",
        "self_scan", "self_capabilities", "self_changes",
        "generate_video", "generate_image",
        "process_file", "list_uploaded_files",
        "capture_webcam", "capture_screen_region",
        "analyze_image_file", "read_image_text", "vision_status",
        "dev_inspect", "dev_test", "dev_search", "dev_propose", "dev_status",
        "declarative_fact", "fact_query",
    }
    if action_name in _NO_RETRY_ACTIONS:
        return execute(action, speak_fn)

    result = execute(action, speak_fn)
    if result[0]:
        return result

    for attempt in range(max_retries):
        print(f"[DEBUG][executor] retrying action={action_name}, attempt={attempt + 1}")
        time.sleep(0.5)
        result = execute(action, speak_fn)
        if result[0]:
            return result

    log_failure(action_name, result[1] or "retry failed")
    print(f"[DEBUG][executor] action failed after retry: {action_name}")
    return result
