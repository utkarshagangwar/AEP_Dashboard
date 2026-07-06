/**
 * GET /api/ai-testing/skills — proxy to FastAPI GET /api/v1/ai-testing/skills
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request) {
  return proxyToFastAPI(request, "/api/v1/ai-testing/skills");
}
