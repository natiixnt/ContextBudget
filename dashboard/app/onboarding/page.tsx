"use client";

import { useState } from "react";

type Step = "welcome" | "connect" | "create-org" | "issue-key" | "done";

const STEPS: Step[] = ["welcome", "connect", "create-org", "issue-key", "done"];

function StepIndicator({ current }: { current: Step }) {
  const labels: Record<Step, string> = {
    welcome: "Welcome",
    connect: "Connect",
    "create-org": "Organisation",
    "issue-key": "API Key",
    done: "Done",
  };
  const idx = STEPS.indexOf(current);
  return (
    <div className="flex items-center gap-2 mb-8">
      {STEPS.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <div
            className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 ${
              i < idx
                ? "bg-emerald-500 border-emerald-500 text-white"
                : i === idx
                ? "bg-accent border-accent text-white"
                : "bg-white/5 border-white/20 text-white/30"
            }`}
          >
            {i < idx ? "✓" : i + 1}
          </div>
          <span className={`text-xs font-medium ${i === idx ? "text-white" : "text-white/30"}`}>
            {labels[s]}
          </span>
          {i < STEPS.length - 1 && <div className="w-8 h-px bg-white/10" />}
        </div>
      ))}
    </div>
  );
}

const inputCls = "w-full bg-page-bg border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent/50";
const btnPrimary = "px-5 py-2.5 bg-accent hover:bg-accent-dark text-white font-semibold rounded-lg transition disabled:opacity-50";
const btnSecondary = "px-4 py-2 border border-white/10 hover:bg-white/5 text-white/60 font-medium rounded-lg text-sm transition disabled:opacity-50";

function WelcomeStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="max-w-lg">
      <h2 className="text-2xl font-bold text-white mb-3">Welcome to Redcon</h2>
      <p className="text-white/50 mb-6">
        This wizard sets up your cloud control plane in about 2 minutes. You will:
      </p>
      <ol className="list-decimal list-inside space-y-2 text-white/60 mb-8 text-sm">
        <li>Connect to your Redcon Cloud instance</li>
        <li>Create your organisation</li>
        <li>Issue an API key for your team</li>
        <li>Copy the setup snippet for your CI / developer machines</li>
      </ol>
      <button onClick={onNext} className={btnPrimary}>Get started →</button>
    </div>
  );
}

function ConnectStep({
  onNext, cloudUrl, setCloudUrl, adminToken, setAdminToken,
}: {
  onNext: () => void;
  cloudUrl: string; setCloudUrl: (v: string) => void;
  adminToken: string; setAdminToken: (v: string) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"ok" | "error" | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch("/api/onboarding/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cloudUrl }),
      });
      if (res.ok) {
        setTestResult("ok");
      } else {
        const j = await res.json();
        setErrorMsg(j.error || "Connection failed");
        setTestResult("error");
      }
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Network error");
      setTestResult("error");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="max-w-lg">
      <h2 className="text-2xl font-bold text-white mb-1">Connect to Cloud</h2>
      <p className="text-white/50 text-sm mb-6">
        Enter the URL of your <code className="bg-white/5 px-1 rounded">redcon-cloud</code> instance.
      </p>
      <div className="space-y-4 mb-6">
        <div>
          <label className="block text-sm font-medium text-white/60 mb-1">Cloud URL</label>
          <input type="url" value={cloudUrl} onChange={(e) => setCloudUrl(e.target.value)} placeholder="https://cloud.example.com" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-white/60 mb-1">Admin Token</label>
          <input type="password" value={adminToken} onChange={(e) => setAdminToken(e.target.value)} placeholder="RC_CLOUD_ADMIN_TOKEN value" className={inputCls} />
          <p className="text-xs text-white/30 mt-1">
            The value of <code className="bg-white/5 px-1 rounded">RC_CLOUD_ADMIN_TOKEN</code> on your cloud server.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button onClick={testConnection} disabled={testing || !cloudUrl} className={btnSecondary}>
          {testing ? "Testing..." : "Test connection"}
        </button>
        <button onClick={onNext} disabled={!cloudUrl || !adminToken} className={btnPrimary}>Continue →</button>
      </div>
      {testResult === "ok" && <p className="mt-3 text-sm text-emerald-400 font-medium">✓ Connected successfully</p>}
      {testResult === "error" && <p className="mt-3 text-sm text-red-400">✗ {errorMsg}</p>}
    </div>
  );
}

function CreateOrgStep({
  onNext, cloudUrl, adminToken, orgId, setOrgId, orgSlug, setOrgSlug,
}: {
  onNext: () => void;
  cloudUrl: string; adminToken: string;
  orgId: number | null; setOrgId: (v: number) => void;
  orgSlug: string; setOrgSlug: (v: string) => void;
}) {
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  async function createOrg() {
    setCreating(true);
    setError("");
    try {
      const res = await fetch("/api/onboarding/create-org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cloudUrl, adminToken, slug: orgSlug, displayName: name }),
      });
      const j = await res.json();
      if (res.ok) setOrgId(j.id);
      else setError(j.error || "Failed to create org");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="max-w-lg">
      <h2 className="text-2xl font-bold text-white mb-1">Create Organisation</h2>
      <p className="text-white/50 text-sm mb-6">
        An organisation is the top-level container for projects, repos, and API keys.
      </p>
      {orgId ? (
        <div className="bg-emerald-900/30 border border-emerald-500/30 rounded-lg p-4 mb-6">
          <p className="text-emerald-400 font-medium text-sm">✓ Organisation <strong>{orgSlug}</strong> created (ID: {orgId})</p>
        </div>
      ) : (
        <div className="space-y-4 mb-6">
          <div>
            <label className="block text-sm font-medium text-white/60 mb-1">Slug</label>
            <input
              type="text"
              value={orgSlug}
              onChange={(e) => setOrgSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))}
              placeholder="my-company"
              className={inputCls}
            />
            <p className="text-xs text-white/30 mt-1">Lowercase letters, numbers, and hyphens only.</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-white/60 mb-1">Display name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="My Company" className={inputCls} />
          </div>
          <button onClick={createOrg} disabled={creating || !orgSlug} className={btnPrimary}>
            {creating ? "Creating..." : "Create organisation"}
          </button>
          {error && <p className="text-sm text-red-400">✗ {error}</p>}
        </div>
      )}
      {orgId && <button onClick={onNext} className={btnPrimary}>Continue →</button>}
    </div>
  );
}

function IssueKeyStep({
  onNext, cloudUrl, adminToken, orgId, rawKey, setRawKey,
}: {
  onNext: () => void;
  cloudUrl: string; adminToken: string;
  orgId: number | null; rawKey: string; setRawKey: (v: string) => void;
}) {
  const [label, setLabel] = useState("default");
  const [issuing, setIssuing] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  async function issueKey() {
    setIssuing(true);
    setError("");
    try {
      const res = await fetch("/api/onboarding/issue-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cloudUrl, adminToken, orgId, label }),
      });
      const j = await res.json();
      if (res.ok) setRawKey(j.raw_key);
      else setError(j.error || "Failed to issue key");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setIssuing(false);
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(rawKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="max-w-lg">
      <h2 className="text-2xl font-bold text-white mb-1">Issue API Key</h2>
      <p className="text-white/50 text-sm mb-6">
        API keys authenticate the CLI and gateway against your cloud instance.
        The raw key is shown <strong className="text-white">once</strong> - copy it immediately.
      </p>
      {!rawKey ? (
        <div className="space-y-4 mb-6">
          <div>
            <label className="block text-sm font-medium text-white/60 mb-1">Label</label>
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="default" className={inputCls} />
          </div>
          <button onClick={issueKey} disabled={issuing || orgId === null} className={btnPrimary}>
            {issuing ? "Issuing..." : "Issue API key"}
          </button>
          {error && <p className="text-sm text-red-400">✗ {error}</p>}
        </div>
      ) : (
        <div className="mb-6">
          <div className="bg-amber-900/30 border border-amber-500/30 rounded-lg p-4 mb-4">
            <p className="text-amber-400 text-sm font-medium mb-2">⚠ Copy this key now - it will not be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-white/5 border border-white/10 rounded px-3 py-2 text-xs font-mono text-white/80 break-all">
                {rawKey}
              </code>
              <button
                onClick={copy}
                className="px-3 py-2 bg-amber-900/50 hover:bg-amber-900/70 text-amber-300 text-xs font-semibold rounded transition whitespace-nowrap"
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          </div>
          <button onClick={onNext} className={btnPrimary}>Continue →</button>
        </div>
      )}
    </div>
  );
}

function DoneStep({ cloudUrl, orgId, rawKey }: { cloudUrl: string; orgId: number | null; rawKey: string }) {
  const [copied, setCopied] = useState(false);
  const snippet = [
    `# Add to your shell profile or .env file`,
    `export RC_GATEWAY_CLOUD_API_KEY="${rawKey}"`,
    `export RC_GATEWAY_CLOUD_POLICY_URL="${cloudUrl}"`,
    `export RC_GATEWAY_CLOUD_ORG_ID="${orgId}"`,
    ``,
    `# Install and initialise in each repo`,
    `pip install redcon`,
    `redcon init`,
  ].join("\n");

  async function copy() {
    await navigator.clipboard.writeText(snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="max-w-lg">
      <h2 className="text-2xl font-bold text-white mb-2">You&apos;re all set!</h2>
      <p className="text-white/50 text-sm mb-6">
        Your organisation is live. Run the snippet below on developer machines and in CI to start
        sending telemetry to your cloud instance.
      </p>
      <div className="bg-page-bg border border-white/10 rounded-xl p-4 mb-4 relative">
        <pre className="text-emerald-400 text-xs font-mono overflow-x-auto whitespace-pre">{snippet}</pre>
        <button
          onClick={copy}
          className="absolute top-3 right-3 px-2 py-1 bg-white/10 hover:bg-white/20 text-white/50 text-xs rounded transition"
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <div className="bg-accent/10 border border-accent/20 rounded-lg p-4 text-sm text-white/70">
        <p className="font-semibold text-white mb-1">Next steps</p>
        <ul className="list-disc list-inside space-y-1">
          <li>Open the <a href="/" className="text-accent-light underline">Overview dashboard</a> to see live data</li>
          <li>Add the GitHub Action to your CI repos</li>
          <li>Set a <code className="bg-white/5 px-1 rounded">max_estimated_input_tokens</code> policy to enforce budgets</li>
        </ul>
      </div>
    </div>
  );
}

export default function OnboardingPage() {
  const [step, setStep] = useState<Step>("welcome");
  const [cloudUrl, setCloudUrl] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [orgId, setOrgId] = useState<number | null>(null);
  const [rawKey, setRawKey] = useState("");

  function next() {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1]);
  }

  return (
    <div>
      <div className="mb-8">
        <span className="text-white/30 text-sm font-medium uppercase tracking-widest">Redcon</span>
        <h1 className="text-3xl font-bold text-white mt-1">Onboarding</h1>
      </div>
      <StepIndicator current={step} />
      <div className="bg-card rounded-2xl border border-white/10 p-8 max-w-2xl">
        {step === "welcome" && <WelcomeStep onNext={next} />}
        {step === "connect" && (
          <ConnectStep onNext={next} cloudUrl={cloudUrl} setCloudUrl={setCloudUrl} adminToken={adminToken} setAdminToken={setAdminToken} />
        )}
        {step === "create-org" && (
          <CreateOrgStep onNext={next} cloudUrl={cloudUrl} adminToken={adminToken} orgId={orgId} setOrgId={setOrgId} orgSlug={orgSlug} setOrgSlug={setOrgSlug} />
        )}
        {step === "issue-key" && (
          <IssueKeyStep onNext={next} cloudUrl={cloudUrl} adminToken={adminToken} orgId={orgId} rawKey={rawKey} setRawKey={setRawKey} />
        )}
        {step === "done" && <DoneStep cloudUrl={cloudUrl} orgId={orgId} rawKey={rawKey} />}
      </div>
    </div>
  );
}
