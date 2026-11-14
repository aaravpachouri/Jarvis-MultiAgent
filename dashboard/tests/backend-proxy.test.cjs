const test = require("node:test");
const assert = require("node:assert/strict");
const { Readable } = require("node:stream");
const { proxyBackendGet } = require("../lib/backend-proxy");
const quantDashboardHandler = require("../api/quant/dashboard");
const quantStockSearchHandler = require("../api/quant/stock-search");
const quantStockQuoteHandler = require("../api/quant/stock-quote");
const quantRefreshHandler = require("../api/quant/refresh");
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


test("Quant dashboard route forwards to the fixed Render endpoint and preserves session cookie", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  let forwardedCookie;
  global.fetch = async (url, options) => {
    requestedUrl = String(url);
    forwardedCookie = options.headers.get("cookie");
    return new Response(JSON.stringify({ error: "Your JARVIS session has expired." }), {
      status: 401,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  };
  try {
    const res = responseRecorder();
    await quantDashboardHandler({
      method: "GET",
      query: {},
      headers: { cookie: "jarvis_dashboard_session=opaque" },
    }, res);
    assert.equal(requestedUrl, "https://jarvis-multiagent-api.onrender.com/api/quant/dashboard");
    assert.equal(forwardedCookie, "jarvis_dashboard_session=opaque");
    assert.equal(res.statusCode, 401);
    assert.equal(JSON.parse(res.body).error, "Your JARVIS session has expired.");
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant stock search route allowlists and safely encodes provider query parameters", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  let forwardedCookie;
  global.fetch = async (url, options) => {
    requestedUrl = String(url);
    forwardedCookie = options.headers.get("cookie");
    return new Response(JSON.stringify({ error: "Your JARVIS session has expired." }), {
      status: 401,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  };
  try {
    const res = responseRecorder();
    await quantStockSearchHandler({
      method: "GET",
      query: { q: "TCS & Co", region: "IN", limit: "12", ignored: "not-forwarded" },
      headers: { cookie: "jarvis_dashboard_session=opaque" },
    }, res);
    assert.equal(
      requestedUrl,
      "https://jarvis-multiagent-api.onrender.com/api/quant/stock-search?q=TCS+%26+Co&region=IN&limit=12"
    );
    assert.equal(forwardedCookie, "jarvis_dashboard_session=opaque");
    assert.equal(res.statusCode, 401);
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant stock quote route forwards only symbol and range to the fixed quote endpoint", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  global.fetch = async url => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({ error: "Your JARVIS session has expired." }), {
      status: 401,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  };
  try {
    const res = responseRecorder();
    await quantStockQuoteHandler({
      method: "GET",
      query: { symbol: "RELIANCE.NS", range: "1mo", host: "https://attacker.invalid" },
      headers: {},
    }, res);
    assert.equal(
      requestedUrl,
      "https://jarvis-multiagent-api.onrender.com/api/quant/stock-quote?symbol=RELIANCE.NS&range=1mo"
    );
    assert.equal(res.statusCode, 401);
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant refresh POST forwards JSON and the signed session cookie to the fixed backend route", async () => {
  const originalFetch = global.fetch;
  let requestedUrl;
  let requestOptions;
  global.fetch = async (url, options) => {
    requestedUrl = String(url);
    requestOptions = options;
    return new Response(JSON.stringify({ ok: true, job_id: "quant-job-123", job: { status: "queued" } }), {
      status: 202,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  };
  try {
    const req = Object.assign(Readable.from([Buffer.from("{}")]), {
      method: "POST",
      headers: {
        cookie: "jarvis_dashboard_session=opaque",
        "content-type": "application/json",
        accept: "application/json",
      },
    });
    const res = responseRecorder();
    await quantRefreshHandler(req, res);
    assert.equal(requestedUrl, "https://jarvis-multiagent-api.onrender.com/api/quant/refresh");
    assert.equal(requestOptions.method, "POST");
    assert.equal(requestOptions.headers.get("cookie"), "jarvis_dashboard_session=opaque");
    assert.equal(requestOptions.headers.get("content-type"), "application/json");
    assert.equal(requestOptions.headers.get("x-forwarded-proto"), "https");
    assert.equal(Buffer.from(requestOptions.body).toString("utf8"), "{}");
    assert.equal(res.statusCode, 202);
    assert.equal(JSON.parse(res.body).job_id, "quant-job-123");
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant refresh route rejects non-POST methods without calling Render", async () => {
  const originalFetch = global.fetch;
  let called = false;
  global.fetch = async () => { called = true; throw new Error("must not call"); };
  try {
    const res = responseRecorder();
    await quantRefreshHandler({ method: "GET", headers: {} }, res);
    assert.equal(res.statusCode, 405);
    assert.equal(res.headers.allow, "POST");
    assert.equal(called, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant refresh route rejects oversized request bodies without calling Render", async () => {
  const originalFetch = global.fetch;
  let called = false;
  global.fetch = async () => { called = true; throw new Error("must not call"); };
  try {
    const req = Object.assign(Readable.from([Buffer.alloc(16 * 1024 + 1, 120)]), {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    const res = responseRecorder();
    await quantRefreshHandler(req, res);
    assert.equal(res.statusCode, 413);
    assert.equal(called, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test("Quant GET routes reject repeated query parameters instead of forwarding ambiguous values", async () => {
  const originalFetch = global.fetch;
  let called = false;
  global.fetch = async () => { called = true; throw new Error("must not call"); };
  try {
    const res = responseRecorder();
    await quantStockSearchHandler({
      method: "GET",
      query: { q: ["TCS", "attacker"] },
      headers: {},
    }, res);
    assert.equal(res.statusCode, 400);
    assert.equal(called, false);
  } finally {
    global.fetch = originalFetch;
  }
});
