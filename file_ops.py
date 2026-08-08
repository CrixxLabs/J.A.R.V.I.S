# file_ops.py — File Manager Module (Phase 2)
# Open, list, search, summarize, rename files using voice
# Fully compatible with conversation_manager

import os
import glob
import datetime
from memory import log_activity
import conversation_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

FOLDERS = {
    "desktop": os.path.join(HOME, "Desktop"),
    "documents": os.path.join(HOME, "Documents"),
    "downloads": os.path.join(HOME, "Downloads"),
    "music": os.path.join(HOME, "Music"),
    "pictures": os.path.join(HOME, "Pictures"),
    "videos": os.path.join(HOME, "Videos"),
}

def open_file(name):
    """Open file by name (searches common folders)"""
    for folder in FOLDERS.values():
        for file in glob.glob(os.path.join(folder, f"*{name}*")):
            try:
                os.startfile(file)
                log_activity("open_file", os.path.basename(file))
                return f"Opening {os.path.basename(file)}."
            except:
                continue
    return f"Couldn't find {name}."


def list_folder(folder="downloads", count=8):
    """List files in a folder"""
    path = FOLDERS.get(folder.lower(), FOLDERS["downloads"])
    try:
        files = os.listdir(path)[:count]
        if not files:
            return f"Nothing in {folder}."
        return f"Top files in {folder}: " + ", ".join(files)
    except Exception as e:
        return f"Couldn't list {folder}."


def search_files(query):
    """Search for files across common folders"""
    results = []
    for folder in FOLDERS.values():
        for file in glob.glob(os.path.join(folder, f"*{query}*")):
            results.append(os.path.basename(file))
    if not results:
        return f"No files found matching '{query}'."
    return "Found: " + ", ".join(results[:6])


def rename_file(old_name, new_name):
    """
    Rename a file by searching common folders for old_name.
    Keeps the original file extension if new_name has none.
    """
    for folder in FOLDERS.values():
        matches = glob.glob(os.path.join(folder, f"*{old_name}*"))
        if matches:
            old_path = matches[0]
            # Preserve extension if new_name doesn't include one
            _, ext = os.path.splitext(old_path)
            if ext and not os.path.splitext(new_name)[1]:
                new_name_full = new_name + ext
            else:
                new_name_full = new_name
            new_path = os.path.join(folder, new_name_full)
            try:
                os.rename(old_path, new_path)
                log_activity("rename_file", f"{os.path.basename(old_path)} → {new_name_full}")
                return f"Renamed to {new_name_full}."
            except Exception as e:
                print(f"[file_ops] rename error: {e}")
                return f"Couldn't rename {old_name}."
    return f"Couldn't find {old_name}."


def summarize_file(name):
    """Summarize a text file using LLM"""
    for folder in FOLDERS.values():
        for file in glob.glob(os.path.join(folder, f"*{name}*")):
            if file.lower().endswith(('.txt', '.md', '.py', '.log')):
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        content = f.read()[:2000]
                    from planner import _ask_fn
                    resp = _ask_fn(f"Summarize this file naturally:\n\n{content}")
                    summary = resp[1] if isinstance(resp, tuple) else resp
                    log_activity("summarize_file", os.path.basename(file))
                    return f"Summary of {os.path.basename(file)}: {summary}"
                except:
                    continue
    return f"Couldn't read or summarize {name}."


def get_file_info(name):
    """Get basic info about a file"""
    for folder in FOLDERS.values():
        for file in glob.glob(os.path.join(folder, f"*{name}*")):
            try:
                size = os.path.getsize(file) / 1024
                modified = datetime.datetime.fromtimestamp(os.path.getmtime(file)).strftime("%d %b %Y")
                return f"{os.path.basename(file)} — {size:.1f} KB, modified {modified}."
            except:
                continue
    return f"Couldn't find {name}."