/**
 * GET /api/ai-testing/runs/:run_id/video
 *
 * Binary proxy to FastAPI GET /api/v1/ai-testing/runs/:run_id/video, for
 * the New Vibe Test / Skill Replay result view's <video> player and
 * Download button.
 *
 * The browser's <video> tag / native downloader cannot send Authorization
 * headers, so the JWT token is passed via query parameter (?token=...) and
 * forwarded as the Authorization header to FastAPI — same trick as the
 * ../stream and ../live-frames proxies. The Range request header (used by
 * <video> for seeking) is forwarded too, and the response status/headers
 * are passed straight through so a 206 Partial Content round-trips intact.
 */
const FASTAPI_BASE = process.env.FASTAPI_URL || "http://backend:8000";

export async function GET(request, { params }) {
  const { run_id } = await params;
  const url = new URL(request.url);

  const tokenFromQuery = url.searchParams.get("token");
  const authHeader = request.headers.get("authorization");
  const token = authHeader || (tokenFromQuery ? `Bearer ${tokenFromQuery}` : "");

  const headers = new Headers();
  if (token) headers.set("authorization", token);
  const range = request.headers.get("range");
  if (range) headers.set("range", range);

  const targetUrl = `${FASTAPI_BASE}/api/v1/ai-testing/runs/${run_id}/video`;

  let fastapiResponse;
  try {
    fastapiResponse = await fetch(targetUrl, { headers });
  } catch (err) {
    console.error("[ai-testing video proxy] FastAPI unreachable:", err.message);
    return new Response("Backend unavailable", { status: 503 });
  }

  const outHeaders = new Headers();
  for (const key of [
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "cache-control",
  ]) {
    const value = fastapiResponse.headers.get(key);
    if (value) outHeaders.set(key, value);
  }

  return new Response(fastapiResponse.body, {
    status: fastapiResponse.status,
    headers: outHeaders,
  });
}
