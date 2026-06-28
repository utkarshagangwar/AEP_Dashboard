import logger from "../utils/logger.js";

export async function GET() {
  logger.info("Health check requested");
  return Response.json({
    status: "ok",
    version: "1.0.0",
    timestamp: new Date().toISOString(),
  });
}
