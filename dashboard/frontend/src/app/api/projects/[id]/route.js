/**
 * GET   /api/projects/:id  — proxy to FastAPI GET   /api/v1/projects/:id
 * PATCH /api/projects/:id  — proxy to FastAPI PATCH /api/v1/projects/:id
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}`);
}

export async function PATCH(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}`);
}
