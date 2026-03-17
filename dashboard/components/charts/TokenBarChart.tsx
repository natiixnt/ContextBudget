"use client";

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import type { TokenChartEntry } from "@/types";

interface Props {
  data: TokenChartEntry[];
}

function fmtK(v: number) {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(0) + "k";
  return String(v);
}

export default function TokenBarChart({ data }: Props) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-48 text-white/30 text-sm">
        No pack run artifacts found.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: "rgba(255,255,255,0.40)" }}
          angle={-35}
          textAnchor="end"
          tickLine={false}
          axisLine={false}
          interval={0}
        />
        <YAxis
          tickFormatter={fmtK}
          tick={{ fontSize: 11, fill: "rgba(255,255,255,0.40)" }}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        <Tooltip
          formatter={(v: number) => v.toLocaleString()}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid rgba(255,255,255,0.10)", background: "#150020", color: "#fff" }}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: "rgba(255,255,255,0.60)" }} />
        <Bar dataKey="input_tokens" name="Input Tokens" fill="#d40012" radius={[3, 3, 0, 0]} />
        <Bar dataKey="saved_tokens" name="Tokens Saved" fill="#10b981" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
