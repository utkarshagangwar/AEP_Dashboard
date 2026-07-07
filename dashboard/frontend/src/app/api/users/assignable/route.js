/**
 * GET /api/users/assignable — proxy to FastAPI GET /api/v1/users/assignable
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/users/assignable");
}
