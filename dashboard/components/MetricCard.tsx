interface MetricCardProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: "blue" | "green" | "amber" | "red" | "default";
  icon?: React.ReactNode;
}

const colorMap: Record<string, string> = {
  blue: "text-accent",
  green: "text-emerald-400",
  amber: "text-amber-400",
  red: "text-accent",
  default: "text-white",
};

export default function MetricCard({ label, value, sub, color = "default", icon }: MetricCardProps) {
  return (
    <div className="bg-card rounded-xl border border-white/10 p-5 flex flex-col gap-2 min-w-[150px]">
      {icon && <div className="text-white/40">{icon}</div>}
      <div className="text-xs font-semibold uppercase tracking-wide text-white/40">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${colorMap[color]}`}>{value}</div>
      {sub && <div className="text-xs text-white/30">{sub}</div>}
    </div>
  );
}
