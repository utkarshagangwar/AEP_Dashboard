/**
 * GET /api/ai-usage/summary  — proxy to FastAPI GET /api/v1/ai-usage/summary
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-usage/summary");
}
