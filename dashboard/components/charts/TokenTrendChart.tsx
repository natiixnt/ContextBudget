"use client";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import type { RunTrendEntry } from "@/types";

interface Props {
  data: RunTrendEntry[];
}

function fmtK(v: number) {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(0) + "k";
  return String(v);
}

export default function TokenTrendChart({ data }: Props) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-48 text-white/30 text-sm">
        No pack run history yet.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "rgba(255,255,255,0.40)" }}
          tickLine={false}
          axisLine={false}
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
        <Line
          type="monotone"
          dataKey="input_tokens"
          name="Input Tokens"
          stroke="#d40012"
          strokeWidth={2}
          dot={{ r: 3, fill: "#d40012" }}
          activeDot={{ r: 5 }}
        />
        <Line
          type="monotone"
          dataKey="saved_tokens"
          name="Tokens Saved"
          stroke="#10b981"
          strokeWidth={2}
          dot={{ r: 3, fill: "#10b981" }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
