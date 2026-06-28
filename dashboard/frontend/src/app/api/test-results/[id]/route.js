/**
 * GET /api/test-results/:id  — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/test-results/${id}`);
}
