/**
 * GET    /api/defects/:id  — proxy to FastAPI
 * PATCH  /api/defects/:id  — proxy to FastAPI
 * DELETE /api/defects/:id  — proxy to FastAPI
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/defects/${id}`);
}

export async function PATCH(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/defects/${id}`);
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/defects/${id}`);
}
