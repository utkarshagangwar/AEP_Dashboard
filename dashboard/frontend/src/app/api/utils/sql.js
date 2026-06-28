/**
 * sql.js — stub
 *
 * Direct DB access is handled exclusively by the FastAPI backend.
 * Next.js API routes MUST use proxyToFastAPI (see utils/proxy.js).
 *
 * This stub exists so any accidental import fails loudly at runtime
 * rather than at build time with a missing-package error.
 */

const sql = () => {
  throw new Error(
    "[sql stub] Direct database access from Next.js is not allowed. " +
      "Use proxyToFastAPI() from utils/proxy.js instead."
  );
};
sql.transaction = sql;

export default sql;
