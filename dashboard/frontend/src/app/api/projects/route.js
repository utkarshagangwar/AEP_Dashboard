/**
 * GET  /api/projects  — proxy to FastAPI GET  /api/v1/projects
 * POST /api/projects  — proxy to FastAPI POST /api/v1/projects
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/projects");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/projects");
}
