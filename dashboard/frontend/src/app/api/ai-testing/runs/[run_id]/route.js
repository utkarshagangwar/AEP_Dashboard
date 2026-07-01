/**
 * GET    /api/ai-testing/runs/:run_id — proxy to FastAPI GET    /api/v1/ai-testing/runs/:run_id
 * DELETE /api/ai-testing/runs/:run_id — proxy to FastAPI DELETE /api/v1/ai-testing/runs/:run_id
 */
import { proxyToFastAPI } from "../../../utils/proxy.js";

export async function GET(request, { params }) {
  const { run_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/runs/${run_id}`);
}

export async function DELETE(request, { params }) {
  const { run_id } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-testing/runs/${run_id}`);
}
