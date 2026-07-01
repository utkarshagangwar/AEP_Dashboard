/**
 * GET /api/ai-testing/runs/:run_id/stream
 *
 * SSE proxy to FastAPI GET /api/v1/ai-testing/runs/:run_id/stream
 *
 * EventSource cannot send Authorization headers, so the JWT token
 * is passed via query parameter (?token=...) and forwarded as the
 * Authorization header to FastAPI.
 */
const FASTAPI_BASE = process.env.FASTAPI_URL || "http://backend:8000";

export async function GET(request, { params }) {
  const { run_id } = await params;
  const url = new URL(request.url);

  const tokenFromQuery = url.searchParams.get("token");
  const authHeader = request.headers.get("authorization");
  const token = authHeader || (tokenFromQuery ? `Bearer ${tokenFromQuery}` : "");

  const clientIp =
    request.headers.get("x-forwarded-for") ||
    request.headers.get("x-real-ip") ||
    "";

  const headers = new Headers({
    Accept: "text/event-stream",
    "Cache-Control": "no-cache",
  });
  if (token) headers.set("authorization", token);
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  const targetUrl = `${FASTAPI_BASE}/api/v1/ai-testing/runs/${run_id}/stream`;

  let fastapiResponse;
  try {
    fastapiResponse = await fetch(targetUrl, { headers });
  } catch (err) {
    console.error("[ai-testing stream proxy] FastAPI unreachable:", err.message);
    return new Response('data: {"error":"Backend unavailable"}\n\n', {
      status: 503,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  if (!fastapiResponse.ok) {
    return new Response(
      `data: {"error":"Stream failed (${fastapiResponse.status})"}\n\n`,
      {
        status: fastapiResponse.status,
        headers: { "Content-Type": "text/event-stream" },
      },
    );
  }

  return new Response(fastapiResponse.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
