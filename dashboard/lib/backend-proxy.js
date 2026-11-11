const BACKEND_ORIGIN = "https://jarvis-multiagent-api.onrender.com";

/**
 * Forward a narrowly-scoped dashboard GET endpoint to the existing backend.
 * The browser remains same-origin; authentication and authorization continue
 * to be enforced by Render using the signed HttpOnly session cookie.
 */
async function proxyBackendGet(req, res, buildPath) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");

  if (req.method !== "GET") {
    res.statusCode = 405;
    res.setHeader("Allow", "GET");
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    return res.end(JSON.stringify({ error: "Method not allowed." }));
  }

  try {
    const path = buildPath(req);
    if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("\\") || path.includes("\n")) {
      res.statusCode = 400;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      return res.end(JSON.stringify({ error: "Invalid API route parameter." }));
    }

    const headers = new Headers();
    for (const name of ["cookie", "accept", "user-agent", "x-forwarded-for"]) {
      const value = req.headers[name];
      if (typeof value === "string" && value) headers.set(name, value);
    }
    headers.set("x-forwarded-proto", "https");

    const upstream = await fetch(new URL(path, BACKEND_ORIGIN), {
      method: "GET",
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    });
    const body = Buffer.from(await upstream.arrayBuffer());

    res.statusCode = upstream.status;
    const contentType = upstream.headers.get("content-type");
    if (contentType) res.setHeader("Content-Type", contentType);
    const cacheControl = upstream.headers.get("cache-control");
    if (cacheControl) res.setHeader("Cache-Control", cacheControl);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Length", String(body.length));
    return res.end(body);
  } catch {
    res.statusCode = 502;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    return res.end(JSON.stringify({
      error: "JARVIS API is temporarily unreachable. Please retry shortly.",
    }));
  }
}

module.exports = { proxyBackendGet };
