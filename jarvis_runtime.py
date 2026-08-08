import time
import threading
from datetime import datetime
import pyttsx3
import speech_recognition as sr

import face_recognition_module
import planner
import executor
import server

engine = pyttsx3.init()
recognizer = sr.Recognizer()

def speak(text):
    try:
        print(f"[Jarvis] {text}")
        engine.say(text)
        engine.runAndWait()
    except:
        pass

def run_server():
    """Runs the Flask server without the reloader to safely run inside a thread."""
    server.app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

def get_dynamic_greeting():
    """Returns a greeting based on the current time."""
    hour = datetime.now().hour
    if hour < 12:
        base = "Good morning"
    elif hour < 18:
        base = "Good afternoon"
    else:
        base = "Good evening"
    return f"{base}. Hope you're doing well."

def get_briefing():
    now = datetime.now().strftime("%I:%M %p")
    return f"The time is {now}. No urgent updates. System is running smoothly."

def is_wake_word(text):
    return "jarvis" in text

if __name__ == "__main__":
    # Start server in background thread
    threading.Thread(target=run_server, daemon=True).start()

    # Start face watcher
    face_recognition_module.start_face_watcher()

    # Wait until recognize_face() returns True
    while not face_recognition_module.recognize_face():
        time.sleep(1)

    speak("Face recognized. Welcome back.")
    speak(f"{get_dynamic_greeting()}, welcome back.")
    brief = get_briefing()
    speak(brief)
    speak("What would you like me to do?")

    # Keep loop alive
    while True:
        try:
            with sr.Microphone() as source:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
            text = recognizer.recognize_google(audio).lower()
        except:
            continue
            
        # WAIT for wake word
        if not is_wake_word(text):
            continue

        speak("I'm listening.")

        # Listen for actual command
        try:
            with sr.Microphone() as source:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            command = recognizer.recognize_google(audio).lower()
        except:
            continue

        if "exit" in command or "sleep" in command or "stop" in command:
            speak("Going to standby.")
            break
            
        action, response, _ = planner.ask(command)

        if action:
            success, exec_msg = executor.execute_with_retry(action)
            if exec_msg:
                response = exec_msg

        if not response:
            speak("I didn't catch that clearly.")
        else:
            speak(response)