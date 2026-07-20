/**
 * POST /api/auth/login — proxy to FastAPI.
 *
 * FastAPI handles credential validation, JWT creation, and audit logging.
 * This route strips refresh_token from the JSON response and sets it as an
 * httpOnly cookie so client JS cannot read it (XSS protection).
 *
 * Also rate-limits by client IP before ever proxying to FastAPI — FastAPI's
 * own 10/min limiter on this endpoint is a second line of defense, but it
 * still costs a network hop + the backend's own auth-check work per attempt.
 * This one turns away obvious brute-forcing earlier and cheaper.
 */
import { proxyToFastAPI } from "../../utils/proxy.js";
import { checkRateLimit, getClientIp, resetRateLimit } from "../../utils/rateLimit.js";

const REFRESH_TOKEN_EXPIRE_DAYS = parseInt(
  process.env.REFRESH_TOKEN_EXPIRE_DAYS || "7",
  10,
);

// NODE_ENV === "production" is true for every Docker build regardless of
// whether the request actually arrived over HTTPS (e.g. hitting the
// frontend container directly on :3000 instead of through nginx's :443) —
// using it to decide the cookie's Secure flag meant the refresh cookie was
// silently dropped by the browser on plain HTTP, breaking session refresh
// entirely. nginx forwards X-Forwarded-Proto on every route (see
// docker/nginx.conf), so check the actual request instead.
function isHttpsRequest(request) {
  const proto = request.headers.get("x-forwarded-proto");
  if (proto) return proto.split(",")[0].trim() === "https";
  return new URL(request.url).protocol === "https:";
}

const LOGIN_RATE_LIMIT_MAX = 5;
const LOGIN_RATE_LIMIT_WINDOW_MS = 15 * 60 * 1000;

export async function POST(request) {
  const ip = getClientIp(request);
  const rateLimitKey = `login:${ip}`;
  const { allowed, retryAfterMs } = checkRateLimit(
    rateLimitKey,
    LOGIN_RATE_LIMIT_MAX,
    LOGIN_RATE_LIMIT_WINDOW_MS,
  );
  if (!allowed) {
    return Response.json(
      { error: "Too many login attempts. Please try again later." },
      {
        status: 429,
        headers: { "Retry-After": String(Math.ceil(retryAfterMs / 1000)) },
      },
    );
  }

  const body = await request.text();

  const fastapiRes = await proxyToFastAPI(request, "/api/v1/auth/login", {
    method: "POST",
    body,
  });

  if (!fastapiRes.ok) return fastapiRes;

  // Successful login — don't let a legitimate user's earlier failed attempts
  // (typos, etc.) count against their next login.
  resetRateLimit(rateLimitKey);

  const data = await fastapiRes.json();
  const { refresh_token, ...clientSafeData } = data;

  const response = Response.json(clientSafeData, { status: 200 });

  if (refresh_token) {
    const cookieParts = [
      `aep_refresh_token=${refresh_token}`,
      "HttpOnly",
      "SameSite=Strict",
      `Max-Age=${REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60}`,
      "Path=/api/auth",
    ];
    if (isHttpsRequest(request)) cookieParts.push("Secure");
    response.headers.set("Set-Cookie", cookieParts.join("; "));
  }

  return response;
}
