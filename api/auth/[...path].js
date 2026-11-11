const BACKEND_ORIGIN = "https://jarvis-multiagent-api.onrender.com";
const ALLOWED_PATHS = new Set([
  "/api/auth/session",
  "/api/auth/login",
  "/api/auth/logout",
]);

// Keep the authentication handshake on the dashboard origin. This avoids
// cross-site cookie restrictions while forwarding the original method/body to
// the existing Render auth implementation.
module.exports = async function handler(req, res) {
  const requestUrl = new URL(req.url || "/", BACKEND_ORIGIN);
  if (!ALLOWED_PATHS.has(requestUrl.pathname)) {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    return res.end(JSON.stringify({ error: "Not found." }));
  }

  if (!["GET", "POST"].includes(req.method || "")) {
    res.statusCode = 405;
    res.setHeader("Allow", "GET, POST");
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    return res.end(JSON.stringify({ error: "Method not allowed." }));
  }

  try {
    const headers = new Headers();
    for (const name of ["cookie", "content-type", "x-forwarded-for"]) {
      const value = req.headers[name];
      if (typeof value === "string" && value) headers.set(name, value);
    }
    headers.set("x-forwarded-proto", "https");

    let body;
    if (req.method === "POST") {
      const chunks = [];
      for await (const chunk of req) chunks.push(Buffer.from(chunk));
      body = Buffer.concat(chunks);
      if (body.length > 4096) {
        res.statusCode = 413;
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        return res.end(JSON.stringify({ error: "Sign-in request is too large." }));
      }
    }

    const upstream = await fetch(requestUrl, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
      signal: AbortSignal.timeout(10000),
    });
    const responseBody = Buffer.from(await upstream.arrayBuffer());

    res.statusCode = upstream.status;
    for (const name of [
      "content-type",
      "cache-control",
      "set-cookie",
      "x-content-type-options",
      "referrer-policy",
      "content-security-policy",
    ]) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Content-Length", String(responseBody.length));
    return res.end(responseBody);
  } catch (error) {
    // Do not return upstream bodies, credentials, cookies, or environment values.
    res.statusCode = 502;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    return res.end(JSON.stringify({
      error: "Authentication service could not be reached. Please retry shortly.",
    }));
  }
};

module.exports.config = { api: { bodyParser: false } };
