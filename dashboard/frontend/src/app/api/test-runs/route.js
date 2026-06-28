/**
 * GET  /api/test-runs  — proxy to FastAPI GET  /api/v1/runs
 * POST /api/test-runs  — proxy to FastAPI POST /api/v1/runs
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/runs");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/runs");
}
