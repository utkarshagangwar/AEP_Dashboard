/**
 * GET /api/reports  — proxy to FastAPI GET /api/v1/reports
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/reports");
}
