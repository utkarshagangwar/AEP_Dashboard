/**
 * Thin proxy utility — forwards Next.js API requests to FastAPI backend.
 *
 * All Next.js /api/* routes use this to avoid duplicating auth/business logic.
 * FastAPI is the single source of truth for auth, RBAC, DB, and execution.
 */

const FASTAPI_BASE = process.env.FASTAPI_URL || "http://backend:8000";

/**
 * Forward an incoming Next.js Request to FastAPI.
 *
 * @param {Request} request   - Incoming Next.js request
 * @param {string}  apiPath   - FastAPI path, e.g. "/api/v1/projects"
 * @param {object}  [options] - Optional overrides: { method, body, headers }
 * @returns {Promise<Response>}
 */
export async function proxyToFastAPI(request, apiPath, options = {}) {
  const url = new URL(request.url);
  const targetUrl = `${FASTAPI_BASE}${apiPath}${url.search}`;

  // Forward relevant headers (auth, content-type, client IP)
  const headers = new Headers();
  const authHeader = request.headers.get("authorization");
  if (authHeader) headers.set("authorization", authHeader);

  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  // Forward client IP so FastAPI rate limiter and audit logs see the real IP
  const clientIp =
    request.headers.get("x-forwarded-for") ||
    request.headers.get("x-real-ip") ||
    "";
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  // Cookie forwarding (needed for httpOnly refresh token on /auth/refresh)
  const cookieHeader = request.headers.get("cookie");
  if (cookieHeader) headers.set("cookie", cookieHeader);

  const method = options.method || request.method;

  let body = options.body;
  if (body === undefined && !["GET", "HEAD", "DELETE"].includes(method)) {
    try {
      body = await request.text();
    } catch {
      body = undefined;
    }
  }

  try {
    const response = await fetch(targetUrl, {
      method,
      headers,
      body: body || undefined,
    });

    // Stream response body back as-is
    const responseBody = await response.arrayBuffer();
    const responseHeaders = new Headers();

    // Forward relevant response headers
    for (const [key, value] of response.headers.entries()) {
      const lower = key.toLowerCase();
      // Skip hop-by-hop and compression headers — Node.js fetch() already
      // decompresses the body, so forwarding these causes ERR_CONTENT_DECODING_FAILED.
      if (
        ["transfer-encoding", "connection", "keep-alive", "te", "trailer", "upgrade",
         "content-encoding", "content-length"].includes(lower)
      ) continue;
      responseHeaders.set(key, value);
    }

    return new Response(responseBody, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (err) {
    console.error(`[proxy] Failed to reach FastAPI at ${targetUrl}:`, err.message);
    return Response.json(
      { error: "Backend service unavailable" },
      { status: 503 }
    );
  }
}
