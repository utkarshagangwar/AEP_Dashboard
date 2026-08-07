/**
 * GET /api/projects/:id/environments — proxy to FastAPI GET
 * PUT /api/projects/:id/environments — proxy to FastAPI PUT (upsert)
 *
 * Backs the per-project environment addresses added in backend migration
 * 0041. Without a configured base URL here, a test run scoped only to a
 * project resolves nowhere to navigate and the agent opens a blank tab.
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/environments`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/projects/${id}/environments`);
}
