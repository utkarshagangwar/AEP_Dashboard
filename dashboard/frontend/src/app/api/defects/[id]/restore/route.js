/**
 * POST /api/defects/:id/restore — proxy to FastAPI
 *
 * Puts a soft-deleted defect back in the list. Admin / QA lead only; the
 * role check lives on the FastAPI route, not here.
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function POST(request, { params }) {
  const { id } = await params;
  return proxyToFastAPI(request, `/api/v1/defects/${id}/restore`);
}
