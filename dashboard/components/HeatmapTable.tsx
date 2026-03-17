"use client";

import { useState, useMemo } from "react";
import type { HeatmapFile } from "@/types";

interface Props {
  files: HeatmapFile[];
}

type SortKey = keyof HeatmapFile;

function pct(v: number) { return (v * 100).toFixed(1) + "%"; }
function fmtNum(v: number) { return v.toLocaleString(); }

export default function HeatmapTable({ files }: Props) {
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("total_compressed_tokens");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  const maxTok = useMemo(() => Math.max(1, ...files.map((f) => f.total_compressed_tokens || 0)), [files]);

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
      <p className="text-white/30 text-sm py-10 text-center">
        No pack artifacts found - run <code className="font-mono bg-white/5 px-1 rounded">redcon pack</code> first.
      </p>
    );
  }

  const thCls = "px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40 cursor-pointer select-none hover:text-white whitespace-nowrap";

  return (
    <div>
      <div className="mb-3">
        <input
          type="text"
          placeholder="Filter files..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="bg-card border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-white/30 w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
        />
      </div>
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full text-sm bg-card">
          <thead className="border-b border-white/10">
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
                <td colSpan={6} className="py-8 text-center text-white/30 text-sm">No results.</td>
              </tr>
            ) : (
              rows.map((f) => {
                const heat = Math.round(((f.total_compressed_tokens || 0) / maxTok) * 100);
                return (
                  <tr key={f.path} className="relative border-b border-white/5 last:border-0 hover:bg-white/5">
                    <td className="px-3 py-2.5 font-mono text-xs text-white/70 relative">
                      <div
                        className="absolute inset-0 pointer-events-none"
                        style={{ background: `linear-gradient(90deg, rgba(212,0,18,${heat / 300}) ${heat}%, transparent ${heat}%)` }}
                      />
                      <span className="relative">{f.path}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-white/80">{fmtNum(f.total_compressed_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-white/40">{fmtNum(f.total_original_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-emerald-400 font-medium">{fmtNum(f.total_saved_tokens)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-white/70">{fmtNum(f.inclusion_count)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-white/70">{pct(f.inclusion_rate)}</td>
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
