/**
 * GET /api/dashboard/stats  — proxy to FastAPI GET /api/v1/dashboard/stats
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/dashboard/stats");
}
