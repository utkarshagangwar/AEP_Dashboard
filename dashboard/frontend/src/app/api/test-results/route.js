/**
 * GET /api/test-results  — proxy to FastAPI GET /api/v1/test-results
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/test-results");
}
