import { useStatus } from "./api/queries";
import { DemoPanel } from "./components/DemoPanel";
import { ProductsTable } from "./components/ProductsTable";
import { RunHeader } from "./components/RunHeader";
import { SourcesStatus } from "./components/SourcesStatus";

export function App() {
  const status = useStatus();
  return (
    <div className="page">
      <RunHeader status={status} />
      <div className="panels">
        <SourcesStatus status={status.data} />
        <DemoPanel
          activeRunId={status.data?.active_run?.id ?? null}
          hasRuns={Boolean(status.data?.latest_run)}
        />
      </div>
      <ProductsTable />
    </div>
  );
}
