/**
 * GET /api/ai-usage  — proxy to FastAPI GET /api/v1/ai-usage
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-usage");
}
