import { Link } from "react-router-dom";
import type { Decision } from "../../api/types";
import { useApi } from "../../components/hooks";
import { Empty, ErrorState, Loading, PageHeader, StatusBadge, fmt } from "../../components/ui";

export default function Decisions() {
  const { data, loading, error, reload } = useApi<Decision[]>("/api/admin/decisions");
  return (
    <>
      <PageHeader eyebrow="Administration" title="Decision history" subtitle="Append-only audit trail: strategy, version, administrator, decision, timestamp and comment or rejection reason." />
      {loading ? <Loading /> : error ? <ErrorState message={error} onRetry={reload} /> : data!.length === 0 ? <Empty title="No decision recorded yet" /> : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead><tr><th className="th">Date</th><th className="th">Strategy</th><th className="th">Version</th><th className="th">Decision</th><th className="th">Administrator</th><th className="th">Comment / reason</th></tr></thead>
            <tbody>{data!.map((d) => (
              <tr key={d.id}>
                <td className="td text-xs">{fmt.date(d.created_at)}</td>
                <td className="td"><Link className="hover:text-gold-dark" to={`/strategies/${d.strategy_id}`}>{d.strategy_name}</Link></td>
                <td className="td">v{d.strategy_version}</td>
                <td className="td"><StatusBadge status={d.decision} /></td>
                <td className="td text-xs">{d.admin_email}</td>
                <td className="td text-sm">{d.comment || "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
