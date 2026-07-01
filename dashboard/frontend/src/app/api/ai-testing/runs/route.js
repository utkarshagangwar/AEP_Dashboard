/**
 * GET  /api/ai-testing/runs — proxy to FastAPI GET  /api/v1/ai-testing/runs
 * POST /api/ai-testing/runs — proxy to FastAPI POST /api/v1/ai-testing/runs
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/runs");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/runs");
}
