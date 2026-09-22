# Launching JARVIS MARK VII

All commands below assume Windows PowerShell and the repository working directory
`D:\J.A.R.V.I.S`. The active voice runtime is `jarvis.py`. `jarvis_runtime.py`
is legacy and must not be used.

## Main / recommended GUI start

Status: **VERIFIED**

```powershell
Set-Location 'D:\J.A.R.V.I.S'
python .\launch_mark_vii.py
```

This ownership-aware launcher:

- attaches to an existing MARK VII runtime only when its `/v1/snapshot` endpoint
  is verified, and never stops that externally owned process;
- otherwise starts one owned `jarvis.py` process using the current Python
  interpreter;
- starts the native WPF project;
- requests clean shutdown of the owned Python process when WPF exits, with
  bounded terminate/kill fallback;
- refuses to create a second owned runtime in the same launcher session.

Use `python .\launch_mark_vii.py --no-build` after a successful WPF build to
skip rebuilding the native client.

## Runtime only

Status: **VERIFIED**

```powershell
Set-Location 'D:\J.A.R.V.I.S'
python .\jarvis.py
```

`jarvis.py` owns the visual SSE bridge, voice resources, observers, reminder
workers, async task queue, proactive loop/scheduler, face watcher, and evolver.
It does not start either GUI. A second runtime fails clearly when it cannot own
the localhost visual bridge (default `127.0.0.1:8765`).

## Browser GUI

Status: **VERIFIED** as a separate browser backend; it is not the canonical
voice runtime and does not attach to `jarvis.py`.

```powershell
Set-Location 'D:\J.A.R.V.I.S'
python .\server.py
Start-Process 'http://127.0.0.1:5000'
```

`server.py` owns the Flask development server on `JARVIS_BIND_HOST` and
`JARVIS_PORT` (defaults `127.0.0.1:5000`). Stop it with Ctrl+C in its terminal.
Do not launch multiple copies on the same port.

## Native WPF GUI only

Status: **VERIFIED**

Start `jarvis.py` separately first if live runtime data is required, then run:

```powershell
Set-Location 'D:\J.A.R.V.I.S'
dotnet run --project .\desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
```

The WPF client is attach-only. It reconnects to `JARVIS_VISUAL_URL` (default
`http://127.0.0.1:8765`) but does not start, own, stop, or kill Python. Closing
WPF alone therefore leaves an externally started runtime running.

`desktop\Jarvis.Desktop.Base` is a legacy/minimal prototype. The active native
project is `desktop\Jarvis.Desktop.Codex`.

## Development mode

Status: **VERIFIED**

```powershell
Set-Location 'D:\J.A.R.V.I.S'
python -m pytest -q tests
dotnet build .\desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
python .\launch_mark_vii.py --no-build
```

The current machine uses Python 3.11 from
`C:\Users\sonur\AppData\Local\Programs\Python\Python311\python.exe`. No
repository `.venv` or `venv` currently exists. A virtual environment is not
hard-coded, but whichever Python 3.11 environment is used must have
`requirements.txt` installed. The WPF project requires the .NET 8 Desktop SDK;
SDK `8.0.424` and Windows Desktop runtime `8.0.30` were verified here.

## Clean shutdown

Status: **VERIFIED**

- Recommended launcher: close the WPF window. The launcher stops only the
  Python runtime it created.
- Runtime only: press Ctrl+C once in the `jarvis.py` terminal.
- Browser backend: press Ctrl+C once in the `server.py` terminal.
- WPF-only attachment: close the WPF window, then separately press Ctrl+C in
  the externally started runtime terminal when that runtime should stop.

Normal runtime shutdown is bounded and idempotent. It signals worker loops,
cancels reminder waits, shuts down the task queue, closes the SSE listener and
audio resources, restores signal handlers, and saves the session. If an owned
runtime ignores the launcher's clean signal beyond the deadline, the launcher
terminates only that owned process.

## Process ownership summary

| Process/resource | Owner |
|---|---|
| `jarvis.py` voice runtime and in-process workers | Its Python process / lifecycle manager |
| SSE bridge on port 8765 | `jarvis.py` |
| Flask server on port 5000 | Separate `server.py` process |
| Native WPF window and SSE client | WPF/dotnet process |
| Runtime started by `launch_mark_vii.py` | Full-GUI launcher |
| Runtime detected before launcher starts | External owner; launcher and WPF do not stop it |

## Unsupported or deprecated paths

- `python .\jarvis_runtime.py` — **LEGACY/DEPRECATED**.
- `desktop\Jarvis.Desktop.Base` — **LEGACY/DEPRECATED** prototype.
- `python .\jarvis.py` automatically opening WPF or a browser — **UNSUPPORTED**.
- WPF automatically owning Python when launched directly — **UNSUPPORTED**;
  use `launch_mark_vii.py` for an owned full-GUI session.
