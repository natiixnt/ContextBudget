import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const { cloudUrl } = await req.json();
    if (!cloudUrl) {
      return NextResponse.json({ error: "cloudUrl is required" }, { status: 400 });
    }

    const url = cloudUrl.replace(/\/$/, "") + "/health";
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) });

    if (!res.ok) {
      return NextResponse.json(
        { error: `Health check returned HTTP ${res.status}` },
        { status: 502 }
      );
    }

    const body = await res.json();
    return NextResponse.json({ status: "ok", version: body.version ?? "unknown" });
  } catch (err: unknown) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Connection failed" },
      { status: 502 }
    );
  }
}
