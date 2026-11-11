const BACKEND_ORIGIN = "https://jarvis-multiagent-api.onrender.com";
const MAX_BODY_BYTES = 16 * 1024;
const ALLOWED_METHODS = new Set(["GET", "POST"]);

module.exports = async function handler(req, res) {
  const method = req.method || "";
  const requestUrl = new URL(req.url || "/", BACKEND_ORIGIN);

  // This is a fixed-origin API proxy, never a user-controlled URL proxy.
  if (requestUrl.origin !== BACKEND_ORIGIN || !requestUrl.pathname.startsWith("/api/")) {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    return res.end(JSON.stringify({ error: "Not found." }));
  }
  if (!ALLOWED_METHODS.has(method)) {
    res.statusCode = 405;
    res.setHeader("Allow", "GET, POST");
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    return res.end(JSON.stringify({ error: "Method not allowed." }));
  }

  try {
    const headers = new Headers();
    for (const name of ["cookie", "content-type", "accept", "x-forwarded-for", "user-agent"]) {
      const value = req.headers[name];
      if (typeof value === "string" && value) headers.set(name, value);
    }
    headers.set("x-forwarded-proto", "https");

    let body;
    if (method === "POST") {
      const chunks = [];
      let totalBytes = 0;
      for await (const chunk of req) {
        const bytes = Buffer.from(chunk);
        totalBytes += bytes.length;
        if (totalBytes > MAX_BODY_BYTES) {
          res.statusCode = 413;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          return res.end(JSON.stringify({ error: "Request body exceeds the 16 KB limit." }));
        }
        chunks.push(bytes);
      }
      body = Buffer.concat(chunks);
    }

    const upstream = await fetch(requestUrl, {
      method,
      headers,
      body,
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
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
    if (typeof upstream.headers.getSetCookie === "function") {
      const cookies = upstream.headers.getSetCookie();
      if (cookies.length) res.setHeader("Set-Cookie", cookies);
    }
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Content-Length", String(responseBody.length));
    return res.end(responseBody);
  } catch (error) {
    // Do not expose upstream response bodies, cookies, credentials, or secrets.
    res.statusCode = 502;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    return res.end(JSON.stringify({
      error: "JARVIS API is temporarily unreachable. Please retry shortly.",
    }));
  }
};

module.exports.config = { api: { bodyParser: false } };
