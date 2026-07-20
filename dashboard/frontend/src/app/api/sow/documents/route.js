/**
 * GET  /api/sow/documents — proxy to FastAPI GET  /api/v1/sow/documents
 * POST /api/sow/documents — proxy to FastAPI POST /api/v1/sow/documents
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/sow/documents");
}

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/sow/documents");
}
