const BACKEND_ORIGIN = "https://jarvis-multiagent-api.onrender.com";
const ALLOWED_PATHS = new Set([
  "/api/auth/session",
  "/api/auth/login",
  "/api/auth/logout",
]);

module.exports = async function handler(req, res) {
  const requestUrl = new URL(req.url || "/", BACKEND_ORIGIN);
  if (!ALLOWED_PATHS.has(requestUrl.pathname) || requestUrl.origin !== BACKEND_ORIGIN) {
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
      let size = 0;
      for await (const chunk of req) {
        const bytes = Buffer.from(chunk);
        size += bytes.length;
        if (size > 4096) {
          res.statusCode = 413;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          return res.end(JSON.stringify({ error: "Sign-in request is too large." }));
        }
        chunks.push(bytes);
      }
      body = Buffer.concat(chunks);
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
    for (const name of ["content-type", "cache-control", "set-cookie", "x-content-type-options", "referrer-policy", "content-security-policy"]) {
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
  } catch {
    res.statusCode = 502;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    return res.end(JSON.stringify({ error: "Authentication service could not be reached. Please retry shortly." }));
  }
};
module.exports.config = { api: { bodyParser: false } };
