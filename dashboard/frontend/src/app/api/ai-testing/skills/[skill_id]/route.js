/**
 * GET    /api/ai-testing/skills/:skill_id — proxy to FastAPI GET    /api/v1/ai-testing/skills/:skill_id
 * PATCH  /api/ai-testing/skills/:skill_id — proxy to FastAPI PATCH  /api/v1/ai-testing/skills/:skill_id
 * DELETE /api/ai-testing/skills/:skill_id — proxy to FastAPI DELETE /api/v1/ai-testing/skills/:skill_id
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { skill_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/skills/${skill_id}`);
}

export async function PATCH(request, { params }) {
  const { skill_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/skills/${skill_id}`);
}

export async function DELETE(request, { params }) {
  const { skill_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/skills/${skill_id}`);
}
