/**
 * GET /api/reports/:id/videos — proxy to FastAPI GET /api/v1/reports/:id/videos
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/reports/${id}/videos`);
}
