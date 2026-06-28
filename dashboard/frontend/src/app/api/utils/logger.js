/**
 * Logger utility — structured logging for all API routes and services.
 * Mirrors Python's logging module levels: debug, info, warn, error.
 */
const LOG_LEVEL = process.env.LOG_LEVEL || "info";

const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const currentLevel = LEVELS[LOG_LEVEL] ?? LEVELS.info;

function formatEntry(level, message, meta = {}) {
  return JSON.stringify({
    timestamp: new Date().toISOString(),
    level,
    message,
    env: process.env.NODE_ENV || "development",
    ...meta,
  });
}

function log(level, message, meta) {
  if (LEVELS[level] < currentLevel) return;
  const entry = formatEntry(level, message, meta);
  if (level === "error") {
    console.error(entry);
  } else if (level === "warn") {
    console.warn(entry);
  } else {
    console.log(entry);
  }
}

const logger = {
  debug: (msg, meta) => log("debug", msg, meta),
  info: (msg, meta) => log("info", msg, meta),
  warn: (msg, meta) => log("warn", msg, meta),
  error: (msg, meta) => log("error", msg, meta),
};

export default logger;
