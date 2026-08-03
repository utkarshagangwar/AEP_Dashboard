"use client";

import { ErrorPanel } from "../components/ui/error-state";

export default function Error({ error, reset }) {
  return <ErrorPanel message={error?.message || "An unexpected error occurred."} action={{ label: "Try again", onClick: reset }} />;
}
