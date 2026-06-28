/**
 * POST /api/auth/logout — proxy to FastAPI, clear httpOnly cookie.
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function POST(request) {
  // Read refresh token from cookie to send to FastAPI for revocation
  const cookieHeader = request.headers.get("cookie") || "";
  const match = cookieHeader.match(/aep_refresh_token=([^;]+)/);
  const refreshToken = match ? match[1] : null;

  const fastapiRes = await proxyToFastAPI(request, "/api/v1/auth/logout", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  const status = fastapiRes.ok ? 200 : fastapiRes.status;
  const data = await fastapiRes.json().catch(() => ({ message: "Logged out" }));

  const response = Response.json(data, { status });

  // Always clear the cookie regardless of FastAPI response
  response.headers.set(
    "Set-Cookie",
    "aep_refresh_token=; HttpOnly; SameSite=Strict; Max-Age=0; Path=/api/auth",
  );

  return response;
}
