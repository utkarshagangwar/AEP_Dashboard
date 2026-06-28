/**
 * POST /api/auth/login — proxy to FastAPI.
 *
 * FastAPI handles credential validation, JWT creation, and audit logging.
 * This route strips refresh_token from the JSON response and sets it as an
 * httpOnly cookie so client JS cannot read it (XSS protection).
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

const REFRESH_TOKEN_EXPIRE_DAYS = parseInt(
  process.env.REFRESH_TOKEN_EXPIRE_DAYS || "7",
  10,
);

export async function POST(request) {
  const body = await request.text();

  const fastapiRes = await proxyToFastAPI(request, "/api/v1/auth/login", {
    method: "POST",
    body,
  });

  if (!fastapiRes.ok) return fastapiRes;

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
    if (process.env.NODE_ENV === "production") cookieParts.push("Secure");
    response.headers.set("Set-Cookie", cookieParts.join("; "));
  }

  return response;
}
