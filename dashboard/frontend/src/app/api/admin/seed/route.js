/**
 * POST /api/admin/seed
 * Proxies to FastAPI POST /api/v1/admin/seed.
 *
 * Seeding logic (password hashing, DB writes, audit log) lives in FastAPI.
 * Protected by SEED_SECRET — the body is forwarded as-is to the backend.
 */
import { proxyToFastAPI } from "../../utils/proxy.js";

export async function POST(request) {
  return proxyToFastAPI(request, "/api/v1/admin/seed");
}
