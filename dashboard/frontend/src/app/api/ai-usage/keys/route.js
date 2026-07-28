/**
 * GET /api/ai-usage/keys  — proxy to FastAPI GET /api/v1/ai-usage/keys
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-usage/keys");
}
