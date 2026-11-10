import { Link } from "react-router-dom";
import type { StrategySummary } from "../../api/types";
import { useApi } from "../../components/hooks";
import { Empty, ErrorState, Loading, PageHeader } from "../../components/ui";
import { StrategyCards } from "../StrategiesList";

export default function ReviewQueue() {
  const { data, loading, error, reload } = useApi<StrategySummary[]>("/api/admin/review-queue");
  const groups = new Map<string, StrategySummary[]>();
  (data ?? []).forEach((s) => groups.set(s.planning_request_id, [...(groups.get(s.planning_request_id) ?? []), s]));
  return (
    <>
      <PageHeader eyebrow="Administration" title="Strategy review queue" subtitle="Proposals awaiting a decision, grouped by planning request. Open a proposal to inspect its results, assumptions and roadmap, then accept, reject or request a revision." />
      {loading ? <Loading /> : error ? <ErrorState message={error} onRetry={reload} /> : groups.size === 0 ? <Empty title="No proposal awaiting review" /> : (
        <div className="space-y-8">
          {[...groups.entries()].map(([rid, items]) => (
            <section key={rid}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div><div className="font-semibold">{items[0].request_title}</div><div className="text-xs text-ink-soft">Requested by {items[0].requester_email}</div></div>
                <div className="flex gap-2">
                  <Link className="btn-outline" to={`/agent/${rid}`}>Conversation</Link>
                  {items[0].problem_type !== "operational" && <Link className="btn-outline" to={`/compare?runs=${items.filter((s) => s.run_id).map((s) => s.run_id).join(",")}`}>Compare alternatives</Link>}
                </div>
              </div>
              <StrategyCards items={items} />
            </section>
          ))}
        </div>
      )}
    </>
  );
}
