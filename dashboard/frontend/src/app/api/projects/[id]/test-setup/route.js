/**
 * GET /api/projects/:id/test-setup — proxy to FastAPI GET
 * PUT /api/projects/:id/test-setup — proxy to FastAPI PUT
 *
 * Backs the single "Test setup" popup: which login this project's tests
 * use, and where they start. One request instead of the previous
 * pick-an-environment-then-save-a-row flow.
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/test-setup`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/test-setup`);
}
