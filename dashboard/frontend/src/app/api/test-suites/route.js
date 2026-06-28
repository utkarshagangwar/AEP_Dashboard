/**
 * GET  /api/test-suites  — proxy to FastAPI GET  /api/v1/test-suites
 * POST /api/test-suites  — proxy to FastAPI POST /api/v1/test-suites
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/test-suites");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/test-suites");
}
