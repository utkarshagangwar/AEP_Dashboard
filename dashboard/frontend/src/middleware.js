import { NextResponse } from "next/server";

// ─── Edge-compatible Base64URL helpers ──────────────────────────────────────
function b64urlDecode(str) {
  let s = str.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const binary = atob(s);
  return binary;
}

function b64urlToUint8Array(str) {
  let s = str.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const binary = atob(s);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function uint8ArrayToB64url(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

// ─── JWT verification (Edge-compatible, Web Crypto API) ─────────────────────
async function verifyJwt(token, secret) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const [headerB64, bodyB64, sigB64] = parts;

    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );

    const signature = b64urlToUint8Array(sigB64);
    const data = encoder.encode(`${headerB64}.${bodyB64}`);
    const valid = await crypto.subtle.verify("HMAC", key, signature, data);
    if (!valid) return null;

    const payload = JSON.parse(b64urlDecode(bodyB64));
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;

    return payload;
  } catch {
    return null;
  }
}

// ─── Route config ───────────────────────────────────────────────────────────

const PUBLIC_ROUTES = ["/", "/login"];
const ADMIN_ROLES = ["admin", "qa_lead"];

const ROLE_ROUTES = {
  "/admin/users": ["admin"],
  "/admin/audit-logs": ["admin", "qa_lead"],
};

// API routes handle their own auth via requireAuth/requireRole
const API_PREFIX = "/api";

// ─── Middleware ──────────────────────────────────────────────────────────────

export async function middleware(request) {
  const { pathname } = request.nextUrl;

  // Skip API routes — they have their own auth guards
  if (pathname.startsWith(API_PREFIX)) {
    return NextResponse.next();
  }

  // Skip public routes — but redirect authenticated users away from /login
  if (PUBLIC_ROUTES.includes(pathname)) {
    if (pathname === "/login") {
      const token = request.cookies.get("aep_token")?.value;
      if (token) {
        const secret = process.env.SECRET_KEY || process.env.AUTH_SECRET;
        if (secret) {
          const payload = await verifyJwt(token, secret);
          if (payload) {
            return NextResponse.redirect(new URL("/dashboard", request.url));
          }
        }
      }
    }
    return NextResponse.next();
  }

  // Skip static assets and Next.js internals
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon") ||
    /\.[a-zA-Z0-9]+$/.test(pathname)
  ) {
    return NextResponse.next();
  }

  // Read token from cookie
  const token = request.cookies.get("aep_token")?.value;

  if (!token) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callback", pathname);
    return NextResponse.redirect(loginUrl);
  }

  // Verify JWT
  const secret = process.env.SECRET_KEY || process.env.AUTH_SECRET;
  if (!secret) {
    // Can't verify — let the request through and let API routes handle it
    return NextResponse.next();
  }

  const payload = await verifyJwt(token, secret);

  if (!payload) {
    // Invalid or expired token — clear cookie and redirect to login
    const response = NextResponse.redirect(new URL("/login", request.url));
    response.cookies.delete("aep_token");
    return response;
  }

  // Check role-based access for admin routes
  for (const [route, allowedRoles] of Object.entries(ROLE_ROUTES)) {
    if (pathname.startsWith(route) && !allowedRoles.includes(payload.role)) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
  }

  // Attach user info to headers so downstream server components can use it
  const response = NextResponse.next();
  response.headers.set("x-user-id", payload.sub || "");
  response.headers.set("x-user-role", payload.role || "");
  response.headers.set("x-user-email", payload.email || "");

  return response;
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public files (images, etc.)
     */
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
