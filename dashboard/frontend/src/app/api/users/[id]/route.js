/**
 * GET    /api/users/:id  — proxy to FastAPI
 * PUT    /api/users/:id  — proxy to FastAPI
 * DELETE /api/users/:id  — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/users/${id}`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/users/${id}`);
}

export async function PATCH(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/users/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/users/${id}`);
}
