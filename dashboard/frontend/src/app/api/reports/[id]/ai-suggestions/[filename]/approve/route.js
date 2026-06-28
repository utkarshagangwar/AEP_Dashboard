/**
 * POST /api/reports/:id/ai-suggestions/:filename/approve — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../../../../utils/proxy.js";

export async function POST(request, { params }) {
  const { id, filename } = await params;
  return proxyToFastAPI(request, `/api/v1/reports/${id}/ai-suggestions/${filename}/approve`);
}
