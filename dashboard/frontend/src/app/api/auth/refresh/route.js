/**
 * POST /api/auth/refresh — proxy to FastAPI.
 *
 * Reads the refresh token from the httpOnly cookie (set at login),
 * forwards it to FastAPI /api/v1/auth/refresh, then rotates the cookie
 * with the new refresh token. Only the new access_token is returned to JS.
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

const REFRESH_TOKEN_EXPIRE_DAYS = parseInt(
  process.env.REFRESH_TOKEN_EXPIRE_DAYS || "7",
  10,
);

// See the matching comment in ../login/route.js — NODE_ENV isn't a proxy
// for "this request arrived over HTTPS," and using it to gate Secure broke
// session refresh entirely when accessed directly on :3000 instead of
// through nginx's :443.
function isHttpsRequest(request) {
  const proto = request.headers.get("x-forwarded-proto");
  if (proto) return proto.split(",")[0].trim() === "https";
  return new URL(request.url).protocol === "https:";
}

export async function POST(request) {
  // Extract refresh token from httpOnly cookie
  const cookieHeader = request.headers.get("cookie") || "";
  const match = cookieHeader.match(/aep_refresh_token=([^;]+)/);
  const refreshToken = match ? match[1] : null;

  if (!refreshToken) {
    return Response.json({ error: "No refresh token" }, { status: 401 });
  }

  const fastapiRes = await proxyToFastAPI(request, "/api/v1/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!fastapiRes.ok) {
    // Clear stale cookie on failure
    const response = await fastapiRes.json().then((d) =>
      Response.json(d, { status: fastapiRes.status })
    );
    response.headers.set(
      "Set-Cookie",
      "aep_refresh_token=; HttpOnly; SameSite=Strict; Max-Age=0; Path=/api/auth",
    );
    return response;
  }

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
