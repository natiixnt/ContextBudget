interface MetricCardProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: "blue" | "green" | "amber" | "red" | "default";
  icon?: React.ReactNode;
}

const colorMap: Record<string, string> = {
  blue: "text-blue-600",
  green: "text-emerald-600",
  amber: "text-amber-600",
  red: "text-red-600",
  default: "text-slate-900",
};

export default function MetricCard({ label, value, sub, color = "default", icon }: MetricCardProps) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm flex flex-col gap-2 min-w-[150px]">
      {icon && <div className="text-slate-400">{icon}</div>}
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${colorMap[color]}`}>{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}
