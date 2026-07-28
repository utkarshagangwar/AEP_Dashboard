/**
 * GET /api/ai-testing/coverage — proxy to FastAPI GET /api/v1/ai-testing/coverage
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/coverage");
}
