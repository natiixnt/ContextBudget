"use client";

import { useState, useMemo } from "react";
import type { HeatmapFile } from "@/types";

interface Props {
  files: HeatmapFile[];
}

type SortKey = keyof HeatmapFile;

function pct(v: number) {
  return (v * 100).toFixed(1) + "%";
}
function fmtNum(v: number) {
  return v.toLocaleString();
}

export default function HeatmapTable({ files }: Props) {
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("total_compressed_tokens");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  const maxTok = useMemo(
    () => Math.max(1, ...files.map((f) => f.total_compressed_tokens || 0)),
    [files]
  );

  const rows = useMemo(() => {
    let data = files.slice();
    if (filter) {
      const q = filter.toLowerCase();
      data = data.filter((f) => f.path.toLowerCase().includes(q));
    }
    data.sort((a, b) => {
      const av = a[sortKey] as number | string;
      const bv = b[sortKey] as number | string;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
      return String(av).localeCompare(String(bv)) * sortDir;
    });
    return data;
  }, [files, filter, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(key); setSortDir(-1); }
  }

  function sortIcon(key: SortKey) {
    if (sortKey !== key) return null;
    return sortDir === 1 ? " ▲" : " ▼";
  }

  if (!files.length) {
    return (
      <p className="text-slate-400 text-sm py-10 text-center">
        No pack artifacts found — run <code className="font-mono bg-slate-100 px-1 rounded">redcon pack</code> first.
      </p>
    );
  }

  const thCls = "px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 cursor-pointer select-none hover:text-slate-800 whitespace-nowrap";

  return (
    <div>
      <div className="mb-3">
        <input
          type="text"
          placeholder="Filter files…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="border border-slate-200 rounded-lg px-3 py-2 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
        />
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
        <table className="w-full text-sm bg-white">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className={thCls} onClick={() => handleSort("path")}>File{sortIcon("path")}</th>
              <th className={`${thCls} text-right`} onClick={() => handleSort("total_compressed_tokens")}>Tokens (compressed){sortIcon("total_compressed_tokens")}</th>
              <th className={`${thCls} text-right`} onClick={() => handleSort("total_original_tokens")}>Tokens (original){sortIcon("total_original_tokens")}</th>
              <th className={`${thCls} text-right`} onClick={() => handleSort("total_saved_tokens")}>Saved{sortIcon("total_saved_tokens")}</th>
              <th className={`${thCls} text-right`} onClick={() => handleSort("inclusion_count")}>Inclusions{sortIcon("inclusion_count")}</th>
              <th className={`${thCls} text-right`} onClick={() => handleSort("inclusion_rate")}>Rate{sortIcon("inclusion_rate")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-8 text-center text-slate-400 text-sm">No results.</td>
              </tr>
            ) : (
              rows.map((f) => {
                const heat = Math.round(((f.total_compressed_tokens || 0) / maxTok) * 100);
                return (
                  <tr key={f.path} className="relative border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-3 py-2.5 font-mono text-xs text-slate-700 relative">
                      <div
                        className="absolute inset-0 pointer-events-none"
                        style={{
                          background: `linear-gradient(90deg, rgba(239,68,68,${heat / 300}) ${heat}%, transparent ${heat}%)`,
                        }}
                      />
                      <span className="relative">{f.path}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{fmtNum(f.total_compressed_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-400">{fmtNum(f.total_original_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-emerald-600 font-medium">{fmtNum(f.total_saved_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{fmtNum(f.inclusion_count)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{pct(f.inclusion_rate)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
