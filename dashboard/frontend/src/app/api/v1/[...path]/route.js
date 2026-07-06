/**
 * Catch-all proxy: /api/v1/* → FastAPI /api/v1/*
 *
 * Every other Next.js API route proxies a specific path (see ../utils/proxy.js),
 * but the Visual QA components (VisualAuditSection, SowCheckpointsSection,
 * FigmaImportSection, AutonomousQASection) call /api/v1/visual-audits/* directly.
 * Without this route those requests 404 on the Next server and the sections
 * feature-detect themselves into rendering nothing.
 *
 * Unlike proxyToFastAPI this forwards bodies as raw bytes so multipart file
 * uploads (PNG references, SOW PDFs, walkthrough videos) survive intact, and
 * returns binary responses (diff/screenshot images) unmodified.
 */

const FASTAPI_BASE = process.env.FASTAPI_URL || "http://backend:8000";

// Hop-by-hop / encoding headers that must not be forwarded back: Node's fetch
// already decompresses bodies, so passing content-encoding through causes
// ERR_CONTENT_DECODING_FAILED in the browser.
const SKIP_RESPONSE_HEADERS = new Set([
  "transfer-encoding",
  "connection",
  "keep-alive",
  "te",
  "trailer",
  "upgrade",
  "content-encoding",
  "content-length",
]);

async function proxy(request, { params }) {
  const { path } = await params;
  const url = new URL(request.url);
  const targetUrl = `${FASTAPI_BASE}/api/v1/${path.join("/")}${url.search}`;

  const headers = new Headers();
  for (const name of ["authorization", "content-type", "cookie"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const clientIp =
    request.headers.get("x-forwarded-for") ||
    request.headers.get("x-real-ip") ||
    "";
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    const buf = await request.arrayBuffer();
    if (buf.byteLength > 0) body = buf;
  }

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
    });

    const responseHeaders = new Headers();
    for (const [key, value] of response.headers.entries()) {
      if (!SKIP_RESPONSE_HEADERS.has(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    }
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (err) {
    console.error(`[proxy] Failed to reach FastAPI at ${targetUrl}:`, err.message);
    return Response.json({ error: "Backend service unavailable" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
