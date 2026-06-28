/**
 * GET /api/audit-logs  — proxy to FastAPI GET /api/v1/audit
 */
import { proxyToFastAPI } from "../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/audit");
}
