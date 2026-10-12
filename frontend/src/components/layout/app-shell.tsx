import { Suspense, type ReactNode } from "react";
import { loadFrontendDataResult } from "@/lib/frontend-data.server";
import { DataError } from "@/components/shared/data-error";
import { ScenarioBarLoader } from "@/components/scenario/scenario-bar-loader";
import { Sidebar } from "./sidebar";
import { TopHeader } from "./top-header";
export async function AppShell({ children, variant = "standard" }: { children: ReactNode; variant?: "standard" | "workspace" }) {
  const result = await loadFrontendDataResult();
  return <div className={`app-shell app-shell--${variant}`}><a href="#main-content" className="skip-link">Skip to content</a><TopHeader data={result.ok ? result.data : undefined} />{result.ok && <Suspense fallback={<div className="scenario-bar scenario-bar--loading" role="status">Loading scenario controls…</div>}><ScenarioBarLoader current={result.scenario} notice={result.notice} /></Suspense>}<div className="app-body">{variant === "standard" && <Sidebar />}<main id="main-content" tabIndex={-1} className="workspace">{result.ok ? children : <DataError message={result.error.message} details={result.error.details} />}</main></div></div>;
}
