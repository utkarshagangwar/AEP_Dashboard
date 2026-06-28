/**
 * GET /api/reports/:id  — proxy to FastAPI GET /api/v1/reports/:id
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/reports/${id}`);
}
