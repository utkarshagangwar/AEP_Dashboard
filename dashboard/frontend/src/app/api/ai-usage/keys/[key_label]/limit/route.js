/**
 * PUT    /api/ai-usage/keys/:key_label/limit  — proxy to FastAPI PUT    /api/v1/ai-usage/keys/:key_label/limit
 * DELETE /api/ai-usage/keys/:key_label/limit  — proxy to FastAPI DELETE /api/v1/ai-usage/keys/:key_label/limit
 *
 * key_label looks like "google:...zGzQg8" — re-encoded here before it goes
 * into the outgoing FastAPI path (Next.js has already URL-decoded it off
 * the incoming request's dynamic segment).
 */
import { proxyToFastAPI } from "../../../../utils/proxy.js";

export async function PUT(request, { params }) {
  const { key_label } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-usage/keys/${encodeURIComponent(key_label)}/limit`);
}

export async function DELETE(request, { params }) {
  const { key_label } = await params;
  return proxyToFastAPI(request, `/api/v1/ai-usage/keys/${encodeURIComponent(key_label)}/limit`);
}
