/**
 * JWT + RBAC utility module
 * Handles token creation, verification, and role-based access control
 */
import crypto from "crypto";
import logger from "./logger.js";

const SECRET_KEY =
  process.env.SECRET_KEY || process.env.AUTH_SECRET || "changeme-set-in-env";
const ACCESS_TOKEN_EXPIRE_MINUTES = parseInt(
  process.env.ACCESS_TOKEN_EXPIRE_MINUTES || "15",
  10,
);
const REFRESH_TOKEN_EXPIRE_DAYS = parseInt(
  process.env.REFRESH_TOKEN_EXPIRE_DAYS || "7",
  10,
);

// ─── Base64URL helpers ────────────────────────────────────────────────────────
function b64urlEncode(str) {
  return Buffer.from(str)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

function b64urlDecode(str) {
  let s = str.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Buffer.from(s, "base64").toString("utf8");
}

// ─── Token creation ───────────────────────────────────────────────────────────
function signToken(payload, expiresInSeconds) {
  const header = b64urlEncode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const now = Math.floor(Date.now() / 1000);
  const body = b64urlEncode(
    JSON.stringify({ ...payload, iat: now, exp: now + expiresInSeconds }),
  );
  const sig = crypto
    .createHmac("sha256", SECRET_KEY)
    .update(`${header}.${body}`)
    .digest("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
  return `${header}.${body}.${sig}`;
}

export function createAccessToken(userId, email, role) {
  logger.debug(`Creating access token for user ${userId}`);
  return signToken(
    { sub: userId, email, role, type: "access" },
    ACCESS_TOKEN_EXPIRE_MINUTES * 60,
  );
}

export function createRefreshToken(userId) {
  logger.debug(`Creating refresh token for user ${userId}`);
  return signToken(
    { sub: userId, type: "refresh" },
    REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
  );
}

// ─── Token verification ───────────────────────────────────────────────────────
export function verifyToken(token) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const [header, body, sig] = parts;
    const expectedSig = crypto
      .createHmac("sha256", SECRET_KEY)
      .update(`${header}.${body}`)
      .digest("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=/g, "");
    if (sig !== expectedSig) {
      logger.warn("Token signature mismatch");
      return null;
    }
    const payload = JSON.parse(b64urlDecode(body));
    if (payload.exp < Math.floor(Date.now() / 1000)) {
      logger.warn("Token expired");
      return null;
    }
    return payload;
  } catch (err) {
    logger.error("Token verification error", { error: err.message });
    return null;
  }
}

// ─── Request auth extraction ──────────────────────────────────────────────────
export function getAuthUser(request) {
  const authHeader = request.headers.get("authorization");
  if (!authHeader?.startsWith("Bearer ")) return null;
  return verifyToken(authHeader.substring(7));
}

// ─── RBAC permission map ──────────────────────────────────────────────────────
const ROLE_PERMISSIONS = {
  admin: ["*"],
  qa_lead: [
    "read:*",
    "write:projects",
    "write:test_suites",
    "write:test_runs",
    "write:test_results",
    "write:defects",
    "write:users",
    "delete:test_suites",
    "delete:test_runs",
    "delete:test_results",
    "delete:defects",
  ],
  qa_engineer: [
    "read:*",
    "write:test_runs",
    "write:test_results",
    "write:defects",
  ],
  developer: ["read:*", "write:defects"],
  viewer: ["read:*"],
};

export function hasPermission(role, permission) {
  const perms = ROLE_PERMISSIONS[role] || [];
  if (perms.includes("*")) return true;
  if (perms.includes(permission)) return true;
  const [action] = permission.split(":");
  return perms.includes(`${action}:*`);
}

// ─── Guard helpers ────────────────────────────────────────────────────────────
export function requireAuth(request) {
  const user = getAuthUser(request);
  if (!user) {
    logger.warn("Unauthenticated request blocked");
    return {
      error: Response.json({ error: "Unauthorized" }, { status: 401 }),
      user: null,
    };
  }
  return { error: null, user };
}

export function requirePermission(request, permission) {
  const { error, user } = requireAuth(request);
  if (error) return { error, user: null };
  if (!hasPermission(user.role, permission)) {
    logger.warn(
      `Permission denied: user ${user.sub} (${user.role}) attempted ${permission}`,
    );
    return {
      error: Response.json({ error: "Forbidden" }, { status: 403 }),
      user: null,
    };
  }
  return { error: null, user };
}

export function requireRole(request, allowedRoles) {
  const { error, user } = requireAuth(request);
  if (error) return { error, user: null };
  if (!allowedRoles.includes(user.role)) {
    logger.warn(
      `Role denied: user ${user.sub} (${user.role}) needs one of [${allowedRoles}]`,
    );
    return {
      error: Response.json({ error: "Forbidden" }, { status: 403 }),
      user: null,
    };
  }
  return { error: null, user };
}

export const REFRESH_TOKEN_EXPIRE_SECONDS =
  REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60;
