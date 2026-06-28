/**
 * GET    /api/execute/:id         — proxy to FastAPI GET /api/v1/runs/:id
 * DELETE /api/execute/:id/cancel  — proxy to FastAPI DELETE /api/v1/runs/:id/cancel
 *
 * Note: SSE stream is handled by /api/execute/:id/stream/route.js
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/runs/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/runs/${id}/cancel`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/runs/${id}/reconcile`);
}
