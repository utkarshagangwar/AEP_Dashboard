import AppShell from "@/components/AppShell";
import ReportGenerationLoader from "@/components/ReportGenerationLoader";

// Wrapped in AppShell so the nav stays visible during the Suspense fallback,
// matching the skeleton this replaced. Without it the loader would blank out
// the whole viewport and make the sidebar unreachable mid-navigation.
export default function Loading() {
  return (
    <AppShell>
      <ReportGenerationLoader />
    </AppShell>
  );
}
