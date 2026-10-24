# Atlas Desktop Agent (Windows)

Atlas can open approved applications, type text, click screen coordinates, use a small allowlist of keyboard shortcuts, open ordinary websites and capture the screen on demand.

## Important architecture boundary

The hosted JARVIS dashboard runs on Vercel and the hosted agent API runs on Render. Neither service can directly send mouse or keyboard input to your personal PC. The desktop companion in this repository runs locally on the Windows computer you want to control. The web page talks to that local companion through `http://127.0.0.1:8766`; it never routes screenshots or desktop input through the cloud API.

## Install and start

1. Install Python 3.12 for Windows.
2. Download or clone this repository's `publish/jarvis-multiagent` branch and extract it to a folder you control.
3. Double-click `run_desktop_agent.bat`. It installs the optional Windows desktop dependencies and starts the companion.
4. The local JARVIS Desktop Companion window displays a pairing key. Use **Copy Pairing Key** in that window.
5. Open the [JARVIS Desktop Agent](https://jarvis-multiagent-dashboard.vercel.app/desktop) page, paste the pairing key and select **Connect**. The key is remembered in this browser on this device. Keep the local companion running while you use desktop control.

If the browser shows a local-network permission prompt, allow access for the JARVIS dashboard. If the page cannot connect, confirm that the companion window is open and that port 8766 is not already being used by another local program.

## Supported tasks

Examples for the Desktop Agent page:

- `open notepad`
- `open calculator`
- `open notepad and type Hello from JARVIS`
- `open https://www.wikipedia.org`
- `search for Python 3.12 release notes`
- `click 620, 420` (screen coordinates are also available by approving a screen capture, then clicking the displayed screenshot)
- `press ctrl+l`
- `wait 1 second`

Allowlisted applications are Notepad, Calculator, File Explorer, Paint, Chrome, Edge, Firefox and VS Code where installed. Each task can contain up to eight simple actions, written on separate lines or separated by semicolons. The parser refuses unknown action types and does not accept shell commands.

## Permission and safety model

- **Every task requires a native approval prompt on the Windows computer.** The exact action sequence, text-entry preview, screen coordinates and current foreground window are shown before execution.
- Screen capture requires a separate approval. The captured image is displayed in the browser tab and is not uploaded to the JARVIS cloud API.
- The server binds to `127.0.0.1` only, checks a long random pairing key with constant-time comparison, and allows browser origins explicitly listed in the code or `JARVIS_DESKTOP_ALLOWED_ORIGINS`.
- There is no remote shell, arbitrary script execution, file deletion, system shutdown, email sending, payment or purchasing action.
- The operating-system input failsafe remains enabled: move the mouse to the top-left screen corner to stop an input sequence.
- Use the agent only while the computer is unlocked and you can see the native approval prompts. Keep the pairing key private, especially on shared computers.

The pairing key is stored under the current Windows user's application-data directory. The companion must be running locally for desktop tasks to work. This initial version uses explicit coordinate clicks; it does not claim semantic understanding of arbitrary buttons from pixels. Use the approved screenshot and coordinate picker for visual targets.

## Troubleshooting

- **Companion won't start:** use Python 3.12 and run the batch file again; read the error in the console.
- **Dependencies missing:** from the repository folder run `py -3.12 -m pip install -e ".[desktop]"`.
- **Browser cannot reach local bridge:** confirm the same PC is running the companion, approve browser local-network access if prompted, and use the canonical HTTPS dashboard URL.
- **A task is rejected:** rephrase it using one of the supported examples. The parser intentionally rejects commands it cannot map confidently.
- **Task starts but a step fails:** Atlas stops at the first failed action and reports which steps finished. Inspect the active application before retrying.
