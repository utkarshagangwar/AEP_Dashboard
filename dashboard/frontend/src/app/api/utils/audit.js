/**
 * Audit log helper — writes a record to audit_logs table.
 */
import sql from "./sql.js";
import logger from "./logger.js";

/**
 * @param {object} opts
 * @param {string} opts.userId
 * @param {string} opts.action   e.g. 'CREATE', 'UPDATE', 'DELETE', 'LOGIN'
 * @param {string} opts.resourceType  e.g. 'user', 'project', 'test_run'
 * @param {string} [opts.resourceId]
 * @param {object} [opts.details]
 * @param {string} [opts.ipAddress]
 */
export async function writeAuditLog({
  userId,
  action,
  resourceType,
  resourceId,
  details,
  ipAddress,
}) {
  try {
    await sql(
      `INSERT INTO audit_logs (user_id, action, resource_type, resource_id, details, ip_address)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [
        userId || null,
        action,
        resourceType,
        resourceId || null,
        details ? JSON.stringify(details) : null,
        ipAddress || null,
      ],
    );
  } catch (err) {
    // Audit failures must never crash the main request
    logger.error("Failed to write audit log", {
      error: err.message,
      action,
      resourceType,
    });
  }
}
