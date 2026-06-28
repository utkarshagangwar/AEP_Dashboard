/**
 * GET /api/auth/me — proxy to FastAPI.
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/auth/me");
}
