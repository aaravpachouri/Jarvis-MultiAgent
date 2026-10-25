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
- Combine up to eight actions with separate lines, semicolons, commas before commands, or the word then.
- Example end-to-end workflow: open [app name] then search for [title/query] then press Enter then click the result.
- You can also send steps as separate commands: open the app, then in the next command type [text] into the search bar, then send Enter, then click the result. Atlas remembers the last search query in the running companion so a later "click the result" can refer to it. Opening a different app clears that search context.
- You can say focus the search bar, click result [title], click the first result, go back, or go forward. These use general Windows UI Automation metadata rather than per-app selectors.

App names not in the fast-launch map are searched using the Windows Start menu. The app must be installed and discoverable by Windows. For app workflows, Atlas inspects the active window's UI Automation tree, ranks visible text fields using accessibility names/automation IDs/help text, and enters the query. If a native/custom-drawn app hides its search field from UI Automation, Atlas can use local Tesseract OCR to find a clearly labelled visible search placeholder (for example, "Search" or "What do you want to play?") and click the detected text bounds; it will not guess if no clear placeholder is found. This visual fallback requires both the Python `pytesseract` package and the Tesseract OCR executable. Atlas then waits for visible controls and searches across accessible list rows, links, cards, custom controls, and title text. App context is preserved between separate dashboard submissions: after opening an app or URL, a follow-up "search for ..." continues in that app instead of unexpectedly opening a separate Google tab. If the foreground browser tab is the JARVIS dashboard itself, Atlas opens the target URL in a separate tab so the controller remains available; it verifies the requested host/path in the browser address bar and waits for the UI to settle. The target tab's runtime identity/title is remembered, and follow-up typing, Enter, and result clicks reactivate that tab even if the dashboard has been brought back to the foreground. When another browser tab is already the foreground target, Atlas reuses it rather than creating an unnecessary tab. If a webpage does not expose its own search field, Atlas falls back to the browser's normal search; when the opened URL identifies a non-search-engine host, it scopes that fallback to the current host using a generic site-restricted query, not a hardcoded site URL. Pressing Enter after search input waits for a stable matching result before the command completes, so the next click cannot race ordinary page loading. Result matching considers both query-token coverage and label-token coverage, so a concise visible title can match a longer search query. When a matching title is text inside a clickable link/list row, Atlas tries the nearest actionable ancestor. It prefers the accessibility Invoke pattern when available; if that has no observable effect, it can click the center of the verified on-screen bounds. In browser contexts it verifies that the title, URL, or tab list changes before reporting the click as successful. App launches wait for a matching foreground window rather than relying on the time it takes Windows to submit a Start-menu search. If the app exposes no usable result control, Atlas also has a visual fallback: it reads visible screen text with local Tesseract OCR, requires a stable text-and-position match across two screenshots, then clicks that verified on-screen bounding box and checks for navigation. This fallback is app-agnostic and the screenshot/OCR run locally. The Python wrapper is installed by run_desktop_agent.bat, but the separate Tesseract OCR executable must also be installed and available on PATH or configured with the TESSERACT_CMD environment variable (for example, C:\Program Files\Tesseract-OCR\tesseract.exe). Without the executable, Atlas keeps the UI Automation path and reports that visual fallback is unavailable. This approach is app-agnostic, but it cannot guarantee support for custom-drawn or inaccessible controls. Coordinate actions request approval because they have ambiguous targets.

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
