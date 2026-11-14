const BACKEND_ORIGIN = "https://jarvis-multiagent-api.onrender.com";
const MAX_POST_BODY_BYTES = 16 * 1024;

function setPrivateResponseHeaders(res) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
}

function sendJsonError(res, statusCode, message, extraHeaders = {}) {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  for (const [name, value] of Object.entries(extraHeaders)) res.setHeader(name, value);
  return res.end(JSON.stringify({ error: message }));
}

/**
 * Construct a query string from an explicit allowlist. Values are taken from
 * Vercel's parsed query object and encoded by URLSearchParams; the browser can
 * never choose the upstream host or endpoint path.
 */
function buildQueryPath(req, pathname, fieldLimits) {
  if (typeof pathname !== "string" || !pathname.startsWith("/api/") || pathname.includes("?") ||
      pathname.includes("\\") || pathname.includes("\n")) return null;

  const params = new URLSearchParams();
  const query = req.query && typeof req.query === "object" ? req.query : {};
  for (const [key, maxLength] of Object.entries(fieldLimits || {})) {
    let value = query[key];
    if (Array.isArray(value)) {
      if (value.length !== 1) return null;
      value = value[0];
    }
    if (value === undefined) continue;
    if (typeof value !== "string" || value.length > maxLength) return null;
    params.set(key, value);
  }
  const search = params.toString();
  return search ? pathname + "?" + search : pathname;
}

/**
 * Forward a narrowly-scoped dashboard GET endpoint to the existing backend.
 * The browser remains same-origin; authentication and authorization continue
 * to be enforced by Render using the signed HttpOnly session cookie.
 */
async function proxyBackendGet(req, res, buildPath) {
  setPrivateResponseHeaders(res);

  if (req.method !== "GET") {
    return sendJsonError(res, 405, "Method not allowed.", { Allow: "GET" });
  }

  try {
    const path = buildPath(req);
    if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("\\") || path.includes("\n")) {
      return sendJsonError(res, 400, "Invalid API route parameter.");
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
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Length", String(body.length));
    return res.end(body);
  } catch {
    return sendJsonError(res, 502, "JARVIS API is temporarily unreachable. Please retry shortly.");
  }
}

/**
 * Forward the fixed Quant refresh POST route. The request body is bounded and
 * forwarded as JSON; session authentication remains enforced by Render.
 */
async function proxyBackendPost(req, res, pathname) {
  setPrivateResponseHeaders(res);

  if (req.method !== "POST") {
    return sendJsonError(res, 405, "Method not allowed.", { Allow: "POST" });
  }
  if (pathname !== "/api/quant/refresh") {
    return sendJsonError(res, 400, "Invalid API route parameter.");
  }

  const contentType = req.headers["content-type"];
  if (typeof contentType !== "string" || contentType.split(";")[0].trim().toLowerCase() !== "application/json") {
    return sendJsonError(res, 415, "Content-Type must be application/json.");
  }

  try {
    const chunks = [];
    let totalBytes = 0;
    for await (const chunk of req) {
      const bytes = Buffer.from(chunk);
      totalBytes += bytes.length;
      if (totalBytes > MAX_POST_BODY_BYTES) {
        return sendJsonError(res, 413, "Request body exceeds the 16 KB limit.");
      }
      chunks.push(bytes);
    }
    if (totalBytes < 1) {
      return sendJsonError(res, 400, "Request body must contain a JSON object.");
    }

    const body = Buffer.concat(chunks);
    const headers = new Headers();
    for (const name of ["cookie", "accept", "user-agent", "x-forwarded-for"]) {
      const value = req.headers[name];
      if (typeof value === "string" && value) headers.set(name, value);
    }
    headers.set("content-type", contentType);
    headers.set("x-forwarded-proto", "https");

    const upstream = await fetch(new URL(pathname, BACKEND_ORIGIN), {
      method: "POST",
      headers,
      body,
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    });
    const responseBody = Buffer.from(await upstream.arrayBuffer());

    res.statusCode = upstream.status;
    const responseType = upstream.headers.get("content-type");
    if (responseType) res.setHeader("Content-Type", responseType);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Length", String(responseBody.length));
    return res.end(responseBody);
  } catch {
    return sendJsonError(res, 502, "JARVIS API is temporarily unreachable. Please retry shortly.");
  }
}

module.exports = { proxyBackendGet, proxyBackendPost, buildQueryPath };
