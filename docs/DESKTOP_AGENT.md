# Atlas Desktop Agent (Windows)

Atlas controls the Windows desktop through a companion running locally on the PC being controlled. Routine tasks run directly. Sensitive or ambiguous actions pause for approval in a native Windows dialog.

## Install and start

1. Use Python 3.12 on Windows 10/11.
2. Update the existing repository folder on branch publish/jarvis-multiagent; do not extract a ZIP on top of a working project.
3. Run run_desktop_agent.bat.
4. Copy the pairing key from the local companion window.
5. Open https://jarvis-multiagent-dashboard.vercel.app/desktop, paste the key and click Connect. Keep the companion running.

Mouse and keyboard input runs locally through http://127.0.0.1:8766. Hosted dashboard/API services do not receive desktop screenshots.

## Supported instruction patterns

- Open familiar apps or any app Windows can find with Start search: open Notepad, open Spotify, open Microsoft Word, open Windows Settings.
- Type text: open Notepad and type Hello from JARVIS; type hello into Discord; type text into the focused field.
- Search the web or open an address: search for Python release notes; open https://www.wikipedia.org.
- Move around the desktop: click 620, 420; double click 100, 200; scroll down; scroll up 750; drag 100, 200 to 300, 400.
- Target named UI Automation controls: click button Refresh; click button Send in Mail.
- Use the supported shortcuts: press ctrl+l, ctrl+f, ctrl+s, alt+tab.
- Combine up to eight actions with separate lines or semicolons.

App names not in the fast-launch map are searched using the Windows Start menu. The app must be installed and discoverable by Windows. Named controls must be exposed through Windows UI Automation. Coordinate actions request approval because Atlas cannot reliably identify their target from coordinates alone.

## Permission model

Low-risk work such as opening ordinary apps or websites, typing ordinary text into a non-sensitive foreground window, scrolling, and routine named controls runs without a popup.

A local approval is required for screen capture, coordinate clicks/drags/double-clicks, sensitive wording or contexts (credentials, system administration, payments, sending/publishing, deletion, installation, sharing, access changes), and long text entry. Controls such as Send, Submit, Delete, Purchase, or Grant access are treated as consequential.

Atlas does not run arbitrary shell commands or scripts and does not offer autonomous file deletion, shutdown, purchasing, or payment workflows. Sensitive UI interactions can still be requested, but they pause for local review; denying a prompt stops the task.

## Security boundaries

- The bridge binds to 127.0.0.1, uses a long random pairing key and constant-time token comparison, and checks allowed browser origins.
- Keep the pairing key private. It is stored under the current Windows user's application-data directory.
- PyAutoGUI's emergency failsafe remains enabled: move the mouse to the top-left corner to stop input.
- Use Atlas while the computer is unlocked and you can see what it is doing. Routine actions intentionally do not display an approval prompt, so avoid leaving sensitive forms or unsaved work focused.

## Troubleshooting

- Companion won't start: use Python 3.12 and read the console error.
- Dependencies missing: run py -3.12 -m pip install -e ".[desktop]" from the repository root.
- Browser cannot reach the bridge: keep the companion running on the same PC and allow the browser's local-network prompt if it appears.
- An app does not open: confirm it is installed and visible from Windows Start search.
- A task is rejected: use explicit steps; unsupported instructions are rejected rather than converted into guesses.
- A step fails after earlier actions ran: Atlas stops at the first failure. Inspect the PC before retrying.
