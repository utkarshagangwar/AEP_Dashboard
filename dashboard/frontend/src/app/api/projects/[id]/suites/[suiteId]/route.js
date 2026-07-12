/**
 * PATCH  /api/projects/:id/suites/:suiteId  — proxy to FastAPI PATCH  /api/v1/projects/:id/suites/:suiteId
 * DELETE /api/projects/:id/suites/:suiteId  — proxy to FastAPI DELETE /api/v1/projects/:id/suites/:suiteId
 */
import { proxyToFastAPI } from "../../../../utils/proxy.js";

export async function PATCH(request, { params }) {
  const { id, suiteId } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/suites/${suiteId}`);
}

export async function DELETE(request, { params }) {
  const { id, suiteId } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/suites/${suiteId}`);
}
