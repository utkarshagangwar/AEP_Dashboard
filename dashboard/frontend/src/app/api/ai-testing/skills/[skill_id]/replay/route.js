/**
 * POST /api/ai-testing/skills/:skill_id/replay — proxy to FastAPI POST /api/v1/ai-testing/skills/:skill_id/replay
 */
import { proxyToFastAPI } from "../../../../utils/proxy.js";

export async function POST(request, { params }) {
  const { skill_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/skills/${skill_id}/replay`);
}
