/**
 * GET /api/reports/:id/ai-suggestions — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/reports/${id}/ai-suggestions`);
}
