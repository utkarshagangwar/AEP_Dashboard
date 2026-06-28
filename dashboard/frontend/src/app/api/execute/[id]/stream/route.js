/**
 * GET /api/execute/:id/stream  — SSE proxy to FastAPI GET /api/v1/runs/:id/stream
 *
 * Streams the FastAPI SSE response directly to the browser.
 * Uses ReadableStream passthrough — no buffering.
 *
 * EventSource doesn't support custom headers, so the JWT token
 * is passed via query parameter and forwarded as Authorization header.
 */
const FASTAPI_BASE = process.env.FASTAPI_URL || "http://backend:8000";

export async function GET(request, { params }) {
  const { id } = await params;
  const url = new URL(request.url);

  // EventSource can't send Authorization header — accept token via query param
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

  const targetUrl = `${FASTAPI_BASE}/api/v1/runs/${id}/stream`;

  let fastapiResponse;
  try {
    fastapiResponse = await fetch(targetUrl, { headers });
  } catch (err) {
    console.error("[stream proxy] FastAPI unreachable:", err.message);
    return new Response("data: {\"error\": \"Backend unavailable\"}\n\n", {
      status: 503,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  if (!fastapiResponse.ok) {
    return new Response(
      `data: {"error": "Stream failed (${fastapiResponse.status})"}\n\n`,
      { status: fastapiResponse.status, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  // Pass the FastAPI SSE body straight through to the browser
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
