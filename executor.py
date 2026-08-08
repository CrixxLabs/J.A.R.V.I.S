# executor.py — Action Executor
# Executes all actions from planner
# Handles multi-step sequences + safety
import subprocess
import webbrowser
import pyautogui
import psutil
import datetime
import os
import json
import glob
import re
import time
import threading
import pyperclip
import smtplib
import pywhatkit
import requests
from mss import mss
import cv2
import numpy as np
import pytesseract
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from dotenv import load_dotenv
from memory import remember, recall_all, log_activity, update_daily_stats, log_failure

# File ops module — provides search_files and rename_file
from file_ops import search_files, rename_file as _rename_file

# Email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

pytesseract.pytesseract.tesseract_cmd = os.getenv(
    "TESSERACT_PATH",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# Injected callbacks
_speak_fn  = None
_ask_fn    = None

# Safety: actions requiring confirmation
CONFIRM_ACTIONS = {"shutdown_pc", "restart_pc"}
_pending_confirm = None

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
        "list files in a folder", "type text into any input"
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
        "generate an image from a text prompt (stub)",
        "generate a short video from a text prompt"
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
}

# ── Phase 5: flat action list for validation ──
AVAILABLE_ACTIONS_LIST = [
    "open_app", "close_app", "join_meeting", "system_control", "play_music",
    "send_message", "read_screen", "web_search",
    "type_text", "media", "scroll","generate_image",
    "generate_video",  "click", "screenshot_describe",
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
    if _spotify:
        return _spotify
    if not SPOTIFY_CLIENT_ID:
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
        return _spotify
    except Exception as e:
        print(f"[Spotify] {e}")
        return None

def spotify_play(query):
    sp = get_spotify()
    if not sp:
        return "Spotify's not set up. add client ID and secret to .env."
    try:
        results = sp.search(q=query, limit=1, type="track")
        tracks  = results["tracks"]["items"]
        if not tracks:
            return f"couldn't find {query} on Spotify"
        t       = tracks[0]
        devices = sp.devices().get("devices", [])
        if not devices:
            return "no active Spotify device. open Spotify on your PC first."
        sp.start_playback(device_id=devices[0]["id"], uris=[t["uri"]])
        time.sleep(1)
        check = sp.current_playback()
        if not check or not check.get("is_playing"):
            return "Spotify didn't start playing."
        log_activity("spotify_play", t["name"])
        return f"playing {t['name']} by {t['artists'][0]['name']}."
    except Exception as e:
        print(f"[Spotify play] {e}")
        return "had trouble with Spotify"

def spotify_control(cmd):
    sp = get_spotify()
    if not sp:
        return "Spotify's not connected"
    try:
        if cmd == "pause":   sp.pause_playback();   return "paused."
        if cmd == "resume":  sp.start_playback();   return "resuming."
        if cmd == "next":    sp.next_track();       return "next track."
        if cmd == "prev":    sp.previous_track();   return "going back."
        if cmd == "like":
            cur = sp.current_playback()
            if cur and cur.get("item"):
                sp.current_user_saved_tracks_add([cur["item"]["id"]])
                return f"liked {cur['item']['name']}."
            return "nothing's playing"
        if cmd == "current":
            cur = sp.current_playback()
            if cur and cur.get("item"):
                return f"playing {cur['item']['name']} by {cur['item']['artists'][0]['name']}."
            return "nothing's playing"
    except Exception as e:
        print(f"[Spotify control] {e}")
        return "Spotify error"


# ── Google Calendar ──
def get_cal_service():
    if not os.path.exists(GCAL_CREDS_FILE):
        return None
    creds = None
    if os.path.exists(GCAL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GCAL_TOKEN_FILE, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS_FILE, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GCAL_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def calendar_today():
    try:
        svc = get_cal_service()
        if not svc:
            return "Google Calendar isn't connected."
        now = datetime.datetime.utcnow().isoformat() + "Z"
        end = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat() + "Z"
        evts = svc.events().list(calendarId="primary", timeMin=now, timeMax=end,
                                  maxResults=5, singleEvents=True, orderBy="startTime"
                                  ).execute().get("items", [])
        if not evts:
            return "nothing on your calendar today."
        lines = []
        for e in evts:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            t = datetime.datetime.fromisoformat(start).strftime("%I:%M %p") if "T" in start else "all day"
            lines.append(f"{e['summary']} at {t}")
        return "today you've got — " + ", ".join(lines) + "."
    except Exception as e:
        print(f"[Calendar] {e}")
        return "couldn't fetch calendar"

def calendar_add(title, date_str, time_str="09:00"):
    try:
        svc = get_cal_service()
        if not svc:
            return "Google Calendar isn't connected."
        try:
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception as e:
            print(f"[executor error] {e}")
            return _stable_failure("Invalid date format.")
        end_dt = dt + datetime.timedelta(hours=1)
        svc.events().insert(calendarId="primary", body={
            "summary": title,
            "start":   {"dateTime": dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end":     {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }).execute()
        log_activity("calendar_add", title)
        return f"added {title} to your calendar."
    except Exception as e:
        print(f"[Calendar add] {e}")
        return "couldn't add that"


# ── Web & content ──
def web_search(query):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        url     = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        res     = requests.get(url, headers=headers, timeout=8)
        soup    = BeautifulSoup(res.text, "html.parser")
        results = [r.get_text(strip=True) for r in soup.select(".result__snippet")[:4] if r.get_text(strip=True)]
        return " | ".join(results)[:1500] if results else "No results."
    except Exception as e:
        print(f"[Search] {e}")
        return "search failed"

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
    weather  = get_weather("your city")
    cal      = calendar_today()
    news     = get_news()
    log_activity("morning_briefing", "completed")
    return f"{greeting} Weather — {weather}. Calendar — {cal}. News — {news}."


# ── OCR + Screenshot ──
def read_screen_text():
    from vision import capture_screen

    img  = capture_screen()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1]
    return pytesseract.image_to_string(gray).strip()


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
    try:
        if not GMAIL_ADDRESS:
            return "email's not set up. add GMAIL_ADDRESS and GMAIL_PASSWORD to .env."
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"]   = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            srv.sendmail(GMAIL_ADDRESS, to, msg.as_string())
        log_activity("email_sent", to)
        return f"email sent to {to}."
    except Exception as e:
        print(f"[Email] {e}")
        return "email failed"

def send_whatsapp(phone, message):
    try:
        now = datetime.datetime.now()
        pywhatkit.sendwhatmsg(phone, message, now.hour, now.minute + 2, wait_time=15, tab_close=True)
        log_activity("whatsapp_sent", phone)
        return f"WhatsApp queued to {phone}."
    except Exception as e:
        print(f"[WhatsApp] {e}")
        return "couldn't send WhatsApp"


# ── PC control ──
def lock_pc():     subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
def shutdown_pc(): subprocess.run("shutdown /s /t 5", shell=True)
def restart_pc():  subprocess.run("shutdown /r /t 5", shell=True)


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
    if "youtu.be" in url:
        return url.split("/")[-1].split("?")[0]
    if "youtube.com" in url and "v=" in url:
        return url.split("v=")[1].split("&")[0]
    return None

def summarize_youtube(url):
    try:
        vid = get_youtube_id(url)
        if not vid:
            return "that URL doesn't look right"
        tl  = YouTubeTranscriptApi.get_transcript(vid)
        txt = " ".join([t["text"] for t in tl])[:4000]
        resp = _ask_fn(f"Summarize naturally in under 5 sentences:\n\n{txt}")
        s = resp[1] if isinstance(resp, tuple) else resp
        return s
    except Exception as e:
        print(f"[YouTube] {e}")
        return "couldn't get the transcript"

def summarize_article(url):
    try:
        res  = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        txt = soup.get_text(separator=" ", strip=True)[:4000]
        resp = _ask_fn(f"Summarize naturally in under 5 sentences:\n\n{txt}")
        s = resp[1] if isinstance(resp, tuple) else resp
        return s
    except Exception as e:
        print(f"[Article] {e}")
        return "had trouble reading that page"


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
        pyautogui.hotkey("ctrl", "d")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "e")
        time.sleep(1)
        for _ in range(10):
            pyautogui.press("tab")
            time.sleep(0.15)
        pyautogui.press("enter")
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
        pyautogui.hotkey("ctrl", "d")
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "e")
        time.sleep(0.8)
        for _ in range(8):
            pyautogui.press("tab")
            time.sleep(0.2)
        pyautogui.press("enter")
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
        pyautogui.scroll(-300)
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
    """
    Generate a short video using kwaivgi/kling-v3.0-std via OpenRouter.
    """
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


def execute(action, speak_fn=None):
    global _pending_confirm
    if not action:
        return _stable_failure("No action to execute.")

    update_daily_stats("actions_executed")
    act = action.get("action", "")
    print(f"[DEBUG][executor] intent detected: {act}")

    try:
        if act in CONFIRM_ACTIONS:
            if _pending_confirm == act:
                _pending_confirm = None
            else:
                _pending_confirm = act
                return _stable_failure(f"Say '{act.replace('_', ' ')}' again to confirm.")

        if act == "open_app":
            return _stable_open_app(action.get("app", ""))
        if act == "close_app":
            return _stable_close_app(action.get("app", ""))
        if act == "join_meeting":
            return _stable_join_meeting(action.get("link", ""), action.get("subject", "class"))
        if act == "media":
            pyautogui.press(action.get("key", "space"))
            _stable_learn_action("media")
            return _stable_success("")
        if act == "scroll":
            pyautogui.scroll(500 if action.get("direction") == "up" else -500)
            _stable_learn_action("scroll")
            return _stable_success("")
        if act == "click":
            pyautogui.click()
            _stable_learn_action("click")
            return _stable_success("")
        if act in {"type_text", "voice_type"}:
            pyautogui.write(action.get("text", ""), interval=0.04)
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
                return _stable_success(web_search(action.get("query", "")))
            except Exception as exc:
                print(f"[DEBUG][executor] web_search error: {exc}")
                return _stable_failure("Search failed.")
        if act == "summarize_url":
            url = action.get("url", "")
            try:
                if "youtube" in url or "youtu.be" in url:
                    return _stable_success(summarize_youtube(url))
                return _stable_success(summarize_article(url))
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
            subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
            return _stable_success("Locking it.")
        if act == "shutdown_pc":
            subprocess.run("shutdown /s /t 5", shell=True)
            return _stable_success("Shutting down in 5.")
        if act == "restart_pc":
            subprocess.run("shutdown /r /t 5", shell=True)
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
            return _stable_success(search_files(query))
        if act == "rename_file":
            old = action.get("old_name", "") or action.get("name", "")
            new = action.get("new_name", "")
            if not old or not new:
                return _stable_failure("I need both the old and new name to rename a file.")
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
        if act in {"send_whatsapp", "send_email", "spotify_play", "spotify_control",
                   "calendar_today", "calendar_add"}:
            return _stable_failure("That feature is disabled for now.")
        if act == "exit":
            return _stable_success("")
        if act == "generate_image":
            return _stable_success(generate_image(
                action.get("prompt", ""), action.get("style", "realistic")
            ))
        if act == "generate_video":
            return _stable_success(generate_video(
                action.get("prompt", ""), int(action.get("duration", 5))
            ))

        # ── Layer 1: Obligation actions ───────────────────────────────────────
        if act == "add_obligation":
            return _handle_add_obligation(action)

        if act == "query_obligations":
            return _handle_query_obligations(action)

        if act == "portal_scan":
            return _handle_portal_scan(action, speak_fn=speak_fn)

        if act == "mark_obligation_done":
            return _handle_mark_obligation_done(action)
        # ─────────────────────────────────────────────────────────────────────

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
        # ─────────────────────────────────────────────────────────────────────

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
        # ─────────────────────────────────────────────────────────────────────

        return _stable_failure("Unsupported action.")
    except Exception as exc:
        print(f"[DEBUG][executor] unhandled error for action {act}: {exc}")
        return _stable_failure("Something went wrong.")


def execute_with_retry(action, speak_fn=None, max_retries=1):
    action_name = action.get("action", "unknown") if isinstance(action, dict) else "unknown"
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
