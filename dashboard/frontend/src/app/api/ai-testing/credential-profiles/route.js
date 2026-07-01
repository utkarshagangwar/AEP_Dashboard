/**
 * GET  /api/ai-testing/credential-profiles — proxy to FastAPI GET
 * POST /api/ai-testing/credential-profiles — proxy to FastAPI POST
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/credential-profiles");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/credential-profiles");
}
