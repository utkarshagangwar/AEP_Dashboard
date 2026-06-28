/**
 * In-memory rate limiter using a sliding window approach.
 * Tracks request counts per key (e.g., IP address) within a time window.
 *
 * NOTE: This is an in-memory store — it resets on server restart and does not
 * share state across multiple serverless instances. For production with
 * multiple replicas, consider Redis-backed rate limiting instead.
 */

const store = new Map();

// Periodic cleanup to prevent memory leaks from stale entries
const CLEANUP_INTERVAL_MS = 60_000;
let lastCleanup = Date.now();

function cleanup() {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  for (const [key, entry] of store) {
    if (entry.resetAt <= now) store.delete(key);
  }
}

/**
 * Check and consume a rate limit bucket.
 *
 * @param {string} key       – Unique identifier (e.g., IP address or email)
 * @param {number} max       – Max requests allowed in the window
 * @param {number} windowMs  – Window duration in milliseconds
 * @returns {{ allowed: boolean, remaining: number, retryAfterMs: number }}
 */
export function checkRateLimit(key, max = 5, windowMs = 15 * 60 * 1000) {
  cleanup();

  const now = Date.now();
  const entry = store.get(key);

  if (!entry || entry.resetAt <= now) {
    // New window
    store.set(key, { count: 1, resetAt: now + windowMs });
    return { allowed: true, remaining: max - 1, retryAfterMs: 0 };
  }

  if (entry.count >= max) {
    const retryAfterMs = entry.resetAt - now;
    return { allowed: false, remaining: 0, retryAfterMs };
  }

  entry.count += 1;
  return { allowed: true, remaining: max - entry.count, retryAfterMs: 0 };
}

/**
 * Get client IP from request headers, falling back to 'unknown'.
 */
export function getClientIp(request) {
  return (
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") ||
    "unknown"
  );
}

/**
 * Reset a rate limit bucket (e.g., after successful login).
 */
export function resetRateLimit(key) {
  store.delete(key);
}
