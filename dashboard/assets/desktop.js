(() => {
  "use strict";

  const BRIDGE = "http://127.0.0.1:8766";
  const STORAGE_KEY = "jarvisDesktopPairingKey";
  const $ = (id) => document.getElementById(id);
  const state = { token: "", connected: false, status: null, toastTimer: null, screenshotUrl: null };

  const setText = (id, text) => { const node = $(id); if (node) node.textContent = String(text ?? ""); };
  function toast(message, error = false) {
    const node = $("toast");
    node.textContent = message;
    node.classList.toggle("error", error);
    node.classList.remove("hidden");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => node.classList.add("hidden"), 5200);
  }
  function setConnected(connected, status = null) {
    state.connected = connected;
    state.status = status;
    $("status-led").className = "led " + (connected ? (status && status.desktop_available ? "good" : "idle") : "bad");
    setText("connection-label", connected ? "LOCAL COMPANION CONNECTED" : "LOCAL COMPANION DISCONNECTED");
    $("preview-button").disabled = !connected;
    $("execute-button").disabled = !connected || !(status && status.desktop_available);
    $("screenshot-button").disabled = !connected || !(status && status.desktop_available);
    if (connected && status) {
      if (status.desktop_available) {
        setText("connection-detail", "Connected · " + status.platform + " · " +
          ((status.screen && status.screen.width) ? status.screen.width + " × " + status.screen.height : "screen detected") +
          " · " + (status.active_window || "active window unknown"));
        $("connection-detail").className = "inline-status good";
      } else {
        setText("connection-detail", status.message || "The companion connected, but desktop input is not available. Run the installer on Windows.");
        $("connection-detail").className = "inline-status bad";
      }
    } else {
      setText("connection-detail", "Not connected. Start the local Windows companion, then connect using its pairing key.");
      $("connection-detail").className = "inline-status";
    }
  }

  async function api(path, options = {}) {
    if (!state.token) throw new Error("Connect the local companion first.");
    let response;
    try {
      response = await fetch(BRIDGE + path, {
        method: options.method || "GET",
        mode: "cors",
        cache: "no-store",
        credentials: "omit",
        headers: {
          "Authorization": "Bearer " + state.token,
          ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {})
        },
        ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {})
      });
    } catch (error) {
      throw new Error("Can't reach the local bridge. Confirm run_desktop_agent.bat is open on this PC and approve the browser's local-network prompt if shown.");
    }
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch {}
      if (response.status === 401) {
        localStorage.removeItem(STORAGE_KEY);
        state.token = "";
        setConnected(false);
      }
      throw new Error(payload.error || ("Local bridge request failed (" + response.status + ")."));
    }
    if (options.responseType === "blob") return response.blob();
    return response.json();
  }

  function renderPlan(plan) {
    const out = $("plan-output");
    out.replaceChildren();
    const heading = document.createElement("strong");
    heading.className = "success";
    heading.textContent = "APPROVAL PLAN · " + plan.actions.length + " STEP" + (plan.actions.length === 1 ? "" : "S");
    out.appendChild(heading);
    const list = document.createElement("ol");
    for (const action of plan.actions) {
      const item = document.createElement("li");
      if (action.type === "open_app") item.textContent = "Open application: " + action.app;
      else if (action.type === "open_url") item.textContent = "Open website: " + action.url;
      else if (action.type === "type_text") {
        const preview = action.text.length > 160 ? action.text.slice(0, 160) + " … [preview]" : action.text;
        item.textContent = "Type text: " + JSON.stringify(preview);
      } else if (action.type === "click") item.textContent = "Click screen position (" + action.x + ", " + action.y + "), " + action.button + " button";
      else if (action.type === "hotkey") item.textContent = "Press shortcut: " + action.keys.join("+");
      else if (action.type === "wait") item.textContent = "Wait " + action.seconds + " seconds";
      else item.textContent = "Unknown action";
      list.appendChild(item);
    }
    out.appendChild(list);
    const footer = document.createElement("p");
    footer.className = "muted";
    footer.textContent = "Preview only. No actions have run. Execution opens a native Windows approval dialog.";
    out.appendChild(footer);
    out.classList.remove("hidden");
  }

  function renderResult(result) {
    const out = $("result-output");
    out.replaceChildren();
    const heading = document.createElement("strong");
    heading.className = result.ok ? "success" : "failure";
    heading.textContent = result.ok ? "TASK COMPLETED" : "TASK STOPPED";
    out.appendChild(heading);
    if (result.completed && result.completed.length) {
      const list = document.createElement("ol");
      for (const step of result.completed) {
        const item = document.createElement("li");
        item.textContent = step.detail || ("Step " + step.step + ": " + step.status);
        list.appendChild(item);
      }
      out.appendChild(list);
    }
    if (result.failed_step) {
      const failed = document.createElement("p");
      failed.className = "failure";
      failed.textContent = "Failed step " + result.failed_step + ": " + (result.error || "No detail provided.");
      out.appendChild(failed);
    }
    if (result.message) {
      const message = document.createElement("p");
      message.textContent = result.message;
      out.appendChild(message);
    }
  }

  async function connect() {
    const value = $("pairing-token").value.trim() || localStorage.getItem(STORAGE_KEY) || "";
    if (!value) {
      toast("Copy the pairing key from the Windows companion window first.", true);
      return;
    }
    state.token = value;
    setText("connection-detail", "Connecting to the local companion…");
    $("connect-button").disabled = true;
    try {
      const status = await api("/v1/status");
      localStorage.setItem(STORAGE_KEY, value);
      $("pairing-token").value = value;
      setConnected(true, status);
      toast(status.desktop_available ? "Desktop companion connected. Actions still require local approval." : "Connected, but desktop dependencies are not ready.", !status.desktop_available);
    } catch (error) {
      setConnected(false);
      setText("connection-detail", error.message);
      $("connection-detail").className = "inline-status bad";
      toast(error.message, true);
    } finally {
      $("connect-button").disabled = false;
    }
  }

  async function previewTask() {
    const task = $("task-input").value.trim();
    if (!task) return toast("Enter the task you want Atlas to perform.", true);
    $("preview-button").disabled = true;
    try {
      const plan = await api("/v1/preview", { method: "POST", body: { task } });
      renderPlan(plan);
    } catch (error) {
      toast(error.message, true);
      if (!state.connected) setConnected(false);
    } finally {
      $("preview-button").disabled = !state.connected;
    }
  }

  async function executeTask(event) {
    if (event) event.preventDefault();
    const task = $("task-input").value.trim();
    if (!task) return toast("Enter a desktop task first.", true);
    $("execute-button").disabled = true;
    setText("result-output", "Waiting for the Windows permission dialog…");
    try {
      const result = await api("/v1/execute", { method: "POST", body: { task } });
      renderResult(result);
      toast(result.ok ? "Approved task completed." : "Task stopped at the first failure.", !result.ok);
      if (!result.ok) $("execute-button").disabled = false;
    } catch (error) {
      const out = $("result-output");
      out.replaceChildren();
      const heading = document.createElement("strong");
      heading.className = "failure";
      heading.textContent = "TASK NOT COMPLETED";
      const paragraph = document.createElement("p");
      paragraph.textContent = error.message;
      out.append(heading, paragraph);
      toast(error.message, true);
    } finally {
      $("execute-button").disabled = !(state.connected && state.status && state.status.desktop_available);
    }
  }

  async function captureScreen() {
    $("screenshot-button").disabled = true;
    setText("coordinate-note", "Waiting for your approval in the Windows companion…");
    try {
      const blob = await api("/v1/screenshot", { method: "POST", responseType: "blob" });
      if (state.screenshotUrl) URL.revokeObjectURL(state.screenshotUrl);
      state.screenshotUrl = URL.createObjectURL(blob);
      $("screen-image").src = state.screenshotUrl;
      $("screen-placeholder").classList.add("hidden");
      $("screen-wrap").classList.remove("hidden");
      setText("coordinate-note", "Screen capture approved. Click the displayed image to insert desktop coordinates into the task.");
      $("coordinate-note").className = "inline-status good";
    } catch (error) {
      setText("coordinate-note", error.message);
      $("coordinate-note").className = "inline-status bad";
      toast(error.message, true);
    } finally {
      $("screenshot-button").disabled = !(state.connected && state.status && state.status.desktop_available);
    }
  }

  function forgetKey() {
    localStorage.removeItem(STORAGE_KEY);
    state.token = "";
    $("pairing-token").value = "";
    setConnected(false);
    toast("Local pairing key forgotten by this browser.");
  }

  $("connect-button").addEventListener("click", connect);
  $("forget-button").addEventListener("click", forgetKey);
  $("preview-button").addEventListener("click", previewTask);
  $("task-form").addEventListener("submit", executeTask);
  $("screenshot-button").addEventListener("click", captureScreen);
  $("task-input").addEventListener("input", () => setText("char-count", $("task-input").value.length + " / 3000"));
  document.querySelectorAll("[data-example]").forEach(button => {
    button.addEventListener("click", () => {
      $("task-input").value = button.getAttribute("data-example") || "";
      $("task-input").dispatchEvent(new Event("input"));
      $("task-input").focus();
    });
  });
  $("screen-image").addEventListener("click", (event) => {
    const image = $("screen-image");
    if (!image.naturalWidth || !image.naturalHeight) return;
    const box = image.getBoundingClientRect();
    const x = Math.max(0, Math.min(image.naturalWidth - 1, Math.round((event.clientX - box.left) * image.naturalWidth / box.width)));
    const y = Math.max(0, Math.min(image.naturalHeight - 1, Math.round((event.clientY - box.top) * image.naturalHeight / box.height)));
    $("task-input").value = "click " + x + ", " + y;
    $("task-input").dispatchEvent(new Event("input"));
    $("task-input").focus();
    setText("coordinate-note", "Added coordinates " + x + ", " + y + ". Review the task and execute to request local approval.");
  });
  $("clear-results").addEventListener("click", () => {
    $("result-output").replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty-result";
    empty.textContent = "Result cleared.";
    $("result-output").appendChild(empty);
  });

  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    $("pairing-token").value = saved;
    state.token = saved;
    connect();
  } else {
    setConnected(false);
  }
})();
