/**
 * POST /api/ai-testing/skills/bulk-assign-project — proxy to FastAPI POST /api/v1/ai-testing/skills/bulk-assign-project
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/skills/bulk-assign-project");
}
