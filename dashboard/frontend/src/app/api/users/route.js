/**
 * GET  /api/users  — proxy to FastAPI GET  /api/v1/users
 * POST /api/users  — proxy to FastAPI POST /api/v1/users
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/users");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/users");
}
