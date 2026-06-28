/**
 * GET /api/reports/:id/videos/:filename — proxy to FastAPI GET /api/v1/reports/:id/videos/:filename
 */
import { proxyToFastAPI } from "../../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id, filename } = await params;
  return proxyToFastAPI(request, `/api/v1/reports/${id}/videos/${filename}`);
}
