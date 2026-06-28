/**
 * GET  /api/defects  — proxy to FastAPI GET  /api/v1/defects
 * POST /api/defects  — proxy to FastAPI POST /api/v1/defects
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/defects");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/defects");
}
