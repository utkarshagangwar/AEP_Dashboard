/**
 * POST /api/projects/discover-suites — proxy to FastAPI POST /api/v1/projects/discover-suites
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/projects/discover-suites");
}
