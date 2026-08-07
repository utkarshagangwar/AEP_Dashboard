/**
 * DELETE /api/projects/:id/environments/:environment — proxy to FastAPI
 *
 * Removes one environment's configured address. See the sibling
 * route.js for why these exist.
 */
import { proxyToFastAPI } from "../../../../utils/proxy.js";

export async function DELETE(request, { params }) {
  const { id, environment } = await params;
  return proxyToFastAPI(
    request,
    `/api/v1/projects/${id}/environments/${encodeURIComponent(environment)}`
  );
}
