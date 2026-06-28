/**
 * GET /api/reports/stats/summary — proxy to FastAPI GET /api/v1/reports/stats/summary
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/reports/stats/summary");
}
