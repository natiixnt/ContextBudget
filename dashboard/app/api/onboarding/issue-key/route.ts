import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const { cloudUrl, adminToken, orgId, label } = await req.json();

    if (!cloudUrl || !orgId) {
      return NextResponse.json(
        { error: "cloudUrl and orgId are required" },
        { status: 400 }
      );
    }

    // Issue the API key - this endpoint accepts any valid Bearer key or admin token
    const url = cloudUrl.replace(/\/$/, "") + `/orgs/${orgId}/api-keys`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({ label: label || "default" }),
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
