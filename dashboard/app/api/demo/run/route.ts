import { NextResponse } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
import { readFile, unlink } from "fs/promises";
import { join } from "path";
import { randomBytes } from "crypto";
import { tmpdir } from "os";

const execFileAsync = promisify(execFile);

const PROJECT_ROOT = process.env.REDCON_ROOT ?? "/Users/naithai/Desktop/amogus/praca/ContextBudget";
const PYTHON = process.env.REDCON_PYTHON ?? `${PROJECT_ROOT}/.venv/bin/python`;
const DEMO_REPO = process.env.REDCON_DEMO_REPO ?? `${PROJECT_ROOT}/redcon`;
const MAX_TOKENS = 8000;

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const task: string = typeof body?.task === "string" ? body.task.trim() : "";
  if (!task) {
    return NextResponse.json({ error: "task is required" }, { status: 400 });
  }

  const id = randomBytes(6).toString("hex");
  const prefix = join(tmpdir(), `redcon-demo-${id}`);
  const jsonPath = `${prefix}.json`;

  try {
    await execFileAsync(
      PYTHON,
      [
        "-m", "redcon.cli", "pack",
        task,
        "--repo", DEMO_REPO,
        "--out-prefix", prefix,
        "--max-tokens", String(MAX_TOKENS),
      ],
      { timeout: 30_000, cwd: PROJECT_ROOT },
    );

    const raw = await readFile(jsonPath, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: msg }, { status: 500 });
  } finally {
    unlink(jsonPath).catch(() => {});
    unlink(`${prefix}.md`).catch(() => {});
  }
}
