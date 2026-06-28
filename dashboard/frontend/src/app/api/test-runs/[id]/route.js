/**
 * GET    /api/test-runs/:id               — proxy to FastAPI GET /api/v1/runs/:id
 * DELETE /api/test-runs/:id               — proxy to FastAPI DELETE /api/v1/runs/:id/cancel
 * DELETE /api/test-runs/:id?action=delete — proxy to FastAPI DELETE /api/v1/runs/:id
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/runs/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  const url = new URL(request.url);
  if (url.searchParams.get("action") === "delete") {
    return proxyToFastAPI(request, `/api/v1/runs/${id}`);
  }
  return proxyToFastAPI(request, `/api/v1/runs/${id}/cancel`);
}
