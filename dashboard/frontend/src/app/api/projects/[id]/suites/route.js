/**
 * GET  /api/projects/:id/suites  — proxy to FastAPI GET  /api/v1/projects/:id/suites
 * POST /api/projects/:id/suites  — proxy to FastAPI POST /api/v1/projects/:id/suites
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/suites`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/suites`);
}
