/**
 * GET    /api/sow/documents/:id — proxy to FastAPI
 * PATCH  /api/sow/documents/:id — proxy to FastAPI
 * DELETE /api/sow/documents/:id — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/sow/documents/${id}`);
}

export async function PATCH(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/sow/documents/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/sow/documents/${id}`);
}
