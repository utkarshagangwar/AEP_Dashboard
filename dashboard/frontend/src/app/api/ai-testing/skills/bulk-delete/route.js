/**
 * POST /api/ai-testing/skills/bulk-delete — proxy to FastAPI POST /api/v1/ai-testing/skills/bulk-delete
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/skills/bulk-delete");
}
