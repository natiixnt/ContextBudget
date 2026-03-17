"use client";

import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";

interface Props {
  used: number;
  saved: number;
}

const COLORS = ["#d40012", "#10b981"];

function fmtK(v: number) {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(0) + "k";
  return String(v);
}

export default function SavingsDonut({ used, saved }: Props) {
  if (used + saved === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-white/30 text-sm">
        No token data yet.
      </div>
    );
  }

  const chartData = [
    { name: "Tokens Used", value: used },
    { name: "Tokens Saved", value: saved },
  ];
  const total = used + saved;

  return (
    <div className="flex flex-col items-center">
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius="55%"
            outerRadius="80%"
            dataKey="value"
            paddingAngle={2}
          >
            {chartData.map((_, i) => (
              <Cell key={i} fill={COLORS[i]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(v: number) => [v.toLocaleString(), ""]}
            contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid rgba(255,255,255,0.10)", background: "#150020", color: "#fff" }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "rgba(255,255,255,0.60)" }} />
        </PieChart>
      </ResponsiveContainer>
      <div className="text-center -mt-2">
        <div className="text-xs text-white/40">Total</div>
        <div className="text-xl font-bold text-white">{fmtK(total)}</div>
      </div>
    </div>
  );
}
