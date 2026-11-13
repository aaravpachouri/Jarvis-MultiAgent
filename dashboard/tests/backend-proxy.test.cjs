const test = require("node:test");
const assert = require("node:assert/strict");
const { proxyBackendGet } = require("../lib/backend-proxy");
const missionHandler = require("../api/missions/[id]");
const athenaBenchmarkHandler = require("../api/athena/benchmark");

function responseRecorder() {
  return {
    headers: {},
    statusCode: 200,
    body: "",
    setHeader(name, value) { this.headers[name.toLowerCase()] = value; },
    end(value = "") { this.body = String(value); },
  };
}

test("nested mission status route forwards to the fixed Render API and preserves session cookie", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  let forwardedCookie;
  global.fetch = async (url, options) => {
    requestedUrl = String(url);
    forwardedCookie = options.headers.get("cookie");
    return new Response(JSON.stringify({ error: "Mission not found in this dashboard session." }), {
      status: 404,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  };
  try {
    const res = responseRecorder();
    await missionHandler({ method: "GET", query: { id: "mission-123" }, headers: { cookie: "jarvis_dashboard_session=opaque" } }, res);
    assert.equal(requestedUrl, "https://jarvis-multiagent-api.onrender.com/api/missions/mission-123");
    assert.equal(forwardedCookie, "jarvis_dashboard_session=opaque");
    assert.equal(res.statusCode, 404);
    assert.match(res.headers["content-type"], /application\/json/);
    assert.equal(JSON.parse(res.body).error, "Mission not found in this dashboard session.");
  } finally {
    global.fetch = originalFetch;
  }
});

test("Athena benchmark route forwards to the fixed Render endpoint and preserves the session cookie", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  let forwardedCookie;
  global.fetch = async (url, options) => {
    requestedUrl = String(url);
    forwardedCookie = options.headers.get("cookie");
    return new Response(JSON.stringify({ error: "Your JARVIS session has expired. Please sign in again." }), {
      status: 401,
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
  };
  try {
    const res = responseRecorder();
    await athenaBenchmarkHandler({
      method: "GET",
      headers: { cookie: "jarvis_dashboard_session=opaque" },
    }, res);
    assert.equal(requestedUrl, "https://jarvis-multiagent-api.onrender.com/api/athena/benchmark");
    assert.equal(forwardedCookie, "jarvis_dashboard_session=opaque");
    assert.equal(res.statusCode, 401);
    assert.ok(res.headers["content-type"].includes("application/json"));
    assert.equal(JSON.parse(res.body).error, "Your JARVIS session has expired. Please sign in again.");
  } finally {
    global.fetch = originalFetch;
  }
});

test("Athena benchmark route rejects non-GET requests without calling Render", async () => {
  const originalFetch = global.fetch;
  let called = false;
  global.fetch = async () => { called = true; throw new Error("must not call"); };
  try {
    const res = responseRecorder();
    await athenaBenchmarkHandler({ method: "POST", headers: {} }, res);
    assert.equal(res.statusCode, 405);
    assert.equal(res.headers.allow, "GET");
    assert.equal(called, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test("nested proxy rejects methods other than GET", async () => {
  const originalFetch = global.fetch;
  let called = false;
  global.fetch = async () => { called = true; throw new Error("must not call"); };
  try {
    const res = responseRecorder();
    await proxyBackendGet({ method: "POST", headers: {} }, res, () => "/api/missions/x");
    assert.equal(res.statusCode, 405);
    assert.equal(res.headers.allow, "GET");
    assert.equal(called, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test("nested proxy returns a safe JSON 502 on upstream failure", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => { throw new Error("sensitive upstream detail"); };
  try {
    const res = responseRecorder();
    await proxyBackendGet({ method: "GET", headers: {} }, res, () => "/api/missions/x");
    assert.equal(res.statusCode, 502);
    assert.match(res.headers["content-type"], /application\/json/);
    assert.doesNotMatch(res.body, /sensitive upstream detail/);
  } finally {
    global.fetch = originalFetch;
  }
});
