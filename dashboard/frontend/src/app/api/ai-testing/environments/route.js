/**
 * GET /api/ai-testing/environments — proxy to FastAPI GET /api/v1/ai-testing/environments
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/environments");
}
