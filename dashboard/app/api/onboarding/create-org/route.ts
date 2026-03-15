import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const { cloudUrl, adminToken, slug, displayName } = await req.json();

    if (!cloudUrl || !adminToken || !slug) {
      return NextResponse.json(
        { error: "cloudUrl, adminToken, and slug are required" },
        { status: 400 }
      );
    }

    const url = cloudUrl.replace(/\/$/, "") + "/orgs";
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({ slug, display_name: displayName || slug }),
      signal: AbortSignal.timeout(10_000),
    });

    const body = await res.json();

    if (!res.ok) {
      return NextResponse.json(
        { error: body.detail ?? `HTTP ${res.status}` },
        { status: res.status }
      );
    }

    return NextResponse.json(body, { status: 201 });
  } catch (err: unknown) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Request failed" },
      { status: 502 }
    );
  }
}
