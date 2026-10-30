import { useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleAlert,
  Code2,
  Cpu,
  FileCode2,
  FlaskConical,
  Layers,
  LoaderCircle,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Upload,
  Wrench,
} from "lucide-react";

type Generated = {
  filename: string;
  proposed_source: string;
  diff: string;
  model: string;
  notes: string;
};

type TestResult = {
  passed: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  command: string;
  sandbox: string;
  failure_analysis?: string;
  note: string;
};

type RepairAttempt = {
  attempt: number;
  proposed_source: string;
  diff: string;
  passed: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  failure_analysis: string;
  status: string;
  note: string;
};

type RepairSessionResult = {
  filename: string;
  verified: boolean;
  final_status: string;
  total_attempts: number;
  attempts: RepairAttempt[];
  final_source: string;
  final_diff: string;
  model: string;
  summary: string;
};

const initialCode = `def apply_discount(price: float, discount_percent: float) -> float:
    discount = price * discount_percent / 100
    return price - discount


if __name__ == "__main__":
    print(apply_discount(100, 120))
`;

const initialTests = `from main import apply_discount

def test_discount_cannot_make_price_negative():
    assert apply_discount(100, 120) == 0

def test_normal_discount():
    assert apply_discount(100, 20) == 80

def test_zero_discount():
    assert apply_discount(50, 0) == 50
`;

const API = "";

export default function App() {
  const [filename, setFilename] = useState("main.py");
  const [source, setSource] = useState(initialCode);
  const [testSource, setTestSource] = useState(initialTests);
  const [bug, setBug] = useState(
    "When the discount is greater than 100%, the total becomes negative. Clamp the discount so the final price cannot be below zero."
  );
  const [testCommand, setTestCommand] = useState<"pytest -q" | "python -m pytest -q" | "python -m unittest">("pytest -q");
  const [showTestEditor, setShowTestEditor] = useState(true);

  // Status and connectivity
  const [modelStatus, setModelStatus] = useState("Checking Ollama…");
  const [dockerStatus, setDockerStatus] = useState("Checking Docker…");

  // State management and concurrency locking
  const [activeAction, setActiveAction] = useState<"none" | "repairing" | "generating" | "testing">("none");
  const [error, setError] = useState("");

  // Session outputs
  const [repairSession, setRepairSession] = useState<RepairSessionResult | null>(null);
  const [selectedAttemptIdx, setSelectedAttemptIdx] = useState<number>(0);
  const [generated, setGenerated] = useState<Generated | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [tab, setTab] = useState<"diff" | "code" | "tests" | "analysis">("diff");

  const isBusy = activeAction !== "none";

  useEffect(() => {
    fetch(`${API}/api/health`)
      .then((r) => r.json())
      .then((d) => {
        if (d.model_connected) {
          setModelStatus(`${d.model} · connected`);
        } else {
          setModelStatus(`${d.model || "Ollama"} · not connected`);
        }
        if (d.docker_available) {
          setDockerStatus("Docker sandbox · active");
        } else {
          setDockerStatus("Docker sandbox · unavailable");
        }
      })
      .catch(() => {
        setModelStatus("Ollama · unreachable");
        setDockerStatus("Docker · unknown");
      });
  }, []);

  // Autonomous Bounded Repair Loop (up to 3 attempts with test evidence)
  async function runAutonomousRepairLoop() {
    if (isBusy) return;
    setActiveAction("repairing");
    setError("");
    setGenerated(null);
    setTestResult(null);
    setRepairSession(null);

    try {
      const res = await fetch(`${API}/api/repair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename,
          source,
          test_source: testSource,
          bug,
          test_command: testCommand,
          max_attempts: 3,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Autonomous repair loop failed");
      setRepairSession(data);
      // Select the last attempt by default (which is either the passing one or final failure)
      setSelectedAttemptIdx((data.attempts?.length || 1) - 1);
      setTab("diff");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unexpected error during repair loop");
    } finally {
      setActiveAction("none");
    }
  }

  // Single-shot patch generation (legacy manual workflow)
  async function generateSinglePatch() {
    if (isBusy) return;
    setActiveAction("generating");
    setError("");
    setTestResult(null);
    setGenerated(null);
    setRepairSession(null);

    try {
      const res = await fetch(`${API}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, source, bug }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Patch generation failed");
      setGenerated(data);
      setTab("diff");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unexpected error");
    } finally {
      setActiveAction("none");
    }
  }

  // Manual test execution in sandbox
  async function runSandboxTests() {
    if (isBusy) return;
    const codeToTest = repairSession
      ? repairSession.attempts[selectedAttemptIdx]?.proposed_source
      : generated
      ? generated.proposed_source
      : source;

    setActiveAction("testing");
    setError("");
    setTestResult(null);

    try {
      const res = await fetch(`${API}/api/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename,
          source: codeToTest,
          test_source: testSource,
          test_command: testCommand,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Test execution failed");
      setTestResult(data);
      setTab("tests");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unexpected error");
    } finally {
      setActiveAction("none");
    }
  }

  function applyActiveProposal() {
    const codeToApply = repairSession
      ? repairSession.attempts[selectedAttemptIdx]?.proposed_source || repairSession.final_source
      : generated?.proposed_source;

    if (codeToApply && window.confirm("Apply this patch to your editor buffer? (This does not touch disk files).")) {
      setSource(codeToApply);
      setGenerated(null);
      setTestResult(null);
      setRepairSession(null);
    }
  }

  async function uploadFile(file?: File) {
    if (!file) return;
    if (!file.name.endsWith(".py")) {
      setError("Please upload a .py Python file.");
      return;
    }
    setFilename(file.name);
    setSource(await file.text());
    setGenerated(null);
    setTestResult(null);
    setRepairSession(null);
    setError("");
  }

  // Compute active attempt or active single-shot patch
  const activeAttempt = repairSession?.attempts?.[selectedAttemptIdx] || null;
  const activeDiff = activeAttempt ? activeAttempt.diff : generated?.diff || "";
  const activeCode = activeAttempt ? activeAttempt.proposed_source : generated?.proposed_source || "";
  const activeStdout = activeAttempt ? activeAttempt.stdout : testResult?.stdout || "";
  const activeStderr = activeAttempt ? activeAttempt.stderr : testResult?.stderr || "";
  const activeAnalysis = activeAttempt ? activeAttempt.failure_analysis : testResult?.failure_analysis || "";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={22} />
          </div>
          <div>
            <b>ProofPatch</b>
            <span>EVIDENCE-GATED AGENT</span>
          </div>
        </div>

        <div className="workspace-label">WORKSPACE</div>
        <button className="nav-item active">
          <Code2 size={17} /> Repair session
        </button>
        <button
          className="nav-item"
          onClick={() => {
            setGenerated(null);
            setTestResult(null);
            setRepairSession(null);
            setError("");
          }}
          disabled={isBusy}
        >
          <Activity size={17} /> New session
        </button>

        <div className="side-card">
          <div className="side-card-icon">
            <Cpu size={18} />
          </div>
          <div>
            <b>Local inference</b>
            <p>{modelStatus}</p>
            <span
              className={
                modelStatus.includes("connected") && !modelStatus.includes("not connected")
                  ? "status-dot ok"
                  : "status-dot"
              }
            />
            <small>Local Ollama provider</small>
          </div>
        </div>

        <div className="side-card">
          <div className="side-card-icon">
            <FlaskConical size={18} />
          </div>
          <div>
            <b>Test isolation</b>
            <p>{dockerStatus}</p>
            <span className={dockerStatus.includes("active") ? "status-dot ok" : "status-dot"} />
            <small>Network disabled · Cap-drop ALL</small>
          </div>
        </div>

        <div className="sidebar-bottom">
          <div className="avatar">PP</div>
          <div>
            <b>Evidence Loop</b>
            <small>Bounded budget (max 3)</small>
          </div>
          <span className="local-tag">LOCAL</span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="crumb">
            Workspace <span>/</span> <b>Autonomous Repair Session</b>
          </div>
          <div className="top-actions">
            <span className="privacy">
              <ShieldCheck size={14} /> Host isolated
            </span>
            <a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">
              API docs <ArrowRight size={13} />
            </a>
          </div>
        </header>

        <section className="hero">
          <div className="eyebrow">
            <Sparkles size={14} /> EVIDENCE-DRIVEN AUTONOMOUS CODING AGENT
          </div>
          <h1>
            Fix the bug.
            <br />
            <em>Prove the patch.</em>
          </h1>
          <p>
            An evidence-gated coding agent that proposes patches, tests them in an isolated Docker container,
            analyzes test failure tracebacks, and iteratively repairs code within a bounded 3-attempt budget.
          </p>
        </section>

        <div className="content-grid">
          {/* INPUT PANEL */}
          <section className="panel input-panel">
            <div className="panel-heading">
              <div>
                <span className="step">01</span>
                <div>
                  <h2>Bug report & specification</h2>
                  <p>Context and requirements for the repair agent.</p>
                </div>
              </div>
              <span className="pill">SPECIFICATION</span>
            </div>

            <label className="field-label">BUG REPORT</label>
            <textarea
              className="bug-input"
              value={bug}
              onChange={(e) => setBug(e.target.value)}
              disabled={isBusy}
              placeholder="Describe the incorrect behavior…"
            />

            <div className="panel-heading file-heading">
              <div>
                <span className="step">02</span>
                <div>
                  <h2>Target Python file</h2>
                  <p>Target source code requiring repair.</p>
                </div>
              </div>
              <label className="upload-btn">
                <Upload size={14} /> Upload .py
                <input
                  type="file"
                  accept=".py,text/x-python"
                  onChange={(e) => uploadFile(e.target.files?.[0])}
                  disabled={isBusy}
                  hidden
                />
              </label>
            </div>

            <div className="file-bar">
              <FileCode2 size={15} />
              <input
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                disabled={isBusy}
                aria-label="Python filename"
              />
              <span>PYTHON SOURCE</span>
            </div>
            <textarea
              className="code-input"
              spellCheck={false}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              disabled={isBusy}
              aria-label="Python source code"
            />
            <div className="editor-footer">
              <span>
                <span className="green-dot" /> Working buffer
              </span>
              <span>
                {source.split("\n").length} lines · {source.length} chars
              </span>
            </div>

            {/* TEST SUITE CONFIGURATION */}
            <div className="panel-heading file-heading">
              <div>
                <span className="step">03</span>
                <div>
                  <h2>Evidence test suite</h2>
                  <p>Tests executed in Docker to verify the patch.</p>
                </div>
              </div>
              <button
                className="upload-btn"
                onClick={() => setShowTestEditor(!showTestEditor)}
                disabled={isBusy}
              >
                {showTestEditor ? "Hide test code" : "Edit tests"}
              </button>
            </div>

            <div className="test-config-row">
              <label className="field-label" style={{ margin: 0 }}>
                TEST RUNNER COMMAND:
              </label>
              <select
                className="command-select"
                value={testCommand}
                onChange={(e) => setTestCommand(e.target.value as any)}
                disabled={isBusy}
              >
                <option value="pytest -q">pytest -q (Default)</option>
                <option value="python -m pytest -q">python -m pytest -q</option>
                <option value="python -m unittest">python -m unittest</option>
              </select>
            </div>

            {showTestEditor && (
              <>
                <div className="file-bar">
                  <FlaskConical size={15} />
                  <span style={{ color: "#dce5f5", fontFamily: "monospace" }}>test_proofpatch.py</span>
                  <span>VERIFICATION TESTS</span>
                </div>
                <textarea
                  className="test-code-input"
                  spellCheck={false}
                  value={testSource}
                  onChange={(e) => setTestSource(e.target.value)}
                  disabled={isBusy}
                  aria-label="Python test code"
                />
              </>
            )}

            {error && (
              <div className="error-box">
                <CircleAlert size={16} />
                <span>{error}</span>
              </div>
            )}

            {/* ACTION BUTTONS WITH CONCURRENCY LOCK */}
            <div className="action-row">
              <button
                className="primary-btn repair-btn"
                disabled={isBusy || !source.trim() || bug.trim().length < 3}
                onClick={runAutonomousRepairLoop}
              >
                {activeAction === "repairing" ? (
                  <>
                    <LoaderCircle className="spin" size={17} /> Running autonomous repair loop…
                  </>
                ) : (
                  <>
                    <RotateCcw size={16} /> Run Autonomous Repair Loop (Max 3) <ArrowRight size={16} />
                  </>
                )}
              </button>
              <button
                className="secondary-action-btn"
                disabled={isBusy || !source.trim() || bug.trim().length < 3}
                onClick={generateSinglePatch}
                title="Generate 1-shot patch without auto-testing"
              >
                {activeAction === "generating" ? (
                  <LoaderCircle className="spin" size={15} />
                ) : (
                  <Wrench size={15} />
                )}
                1-Shot
              </button>
            </div>

            <p className="hint">
              Evidence-gated policy: Patches are never marked verified unless test execution succeeds inside the sandbox.
            </p>
          </section>

          {/* RESULTS PANEL */}
          <section className="panel results-panel">
            <div className="panel-heading">
              <div>
                <span className="step">04</span>
                <div>
                  <h2>Verification workspace</h2>
                  <p>Inspect iterative attempts, test evidence, and diffs.</p>
                </div>
              </div>
              <span
                className={`pill ${
                  repairSession?.verified
                    ? "pill-ready"
                    : repairSession
                    ? "pill-fail"
                    : generated
                    ? "pill-ready"
                    : ""
                }`}
              >
                {repairSession?.verified
                  ? "VERIFIED PATCH"
                  : repairSession
                  ? "UNVERIFIED"
                  : generated
                  ? "PROPOSAL READY"
                  : "AWAITING AGENT"}
              </span>
            </div>

            {/* RESULT SUMMARY BANNER */}
            <div className="result-summary">
              <div className="summary-icon">
                {repairSession?.verified ? (
                  <ShieldCheck size={20} style={{ color: "#43c79e" }} />
                ) : repairSession ? (
                  <ShieldAlert size={20} style={{ color: "#f87171" }} />
                ) : (
                  <FlaskConical size={19} />
                )}
              </div>
              <div className="summary-text">
                <b>
                  {repairSession
                    ? repairSession.verified
                      ? `Verified Patch (Attempt ${repairSession.total_attempts} of 3)`
                      : `Repair Failed (${repairSession.total_attempts} attempts)`
                    : testResult
                    ? testResult.passed
                      ? "Tests passed"
                      : "Tests failed"
                    : generated
                    ? "Single-shot patch generated"
                    : "No repair session active"}
                </b>
                <span>
                  {repairSession
                    ? repairSession.summary
                    : testResult
                    ? `Exit code ${testResult.exit_code} · ${testResult.command}`
                    : generated
                    ? `Generated by ${generated.model}. Run tests to verify.`
                    : "Run the autonomous repair loop to generate and verify a patch with tests."}
                </span>
              </div>
              {repairSession && (
                <span className={`result-badge ${repairSession.verified ? "pass" : "fail"}`}>
                  {repairSession.verified ? <Check size={13} /> : <CircleAlert size={13} />}
                  {repairSession.verified ? "VERIFIED" : "UNVERIFIED"}
                </span>
              )}
              {!repairSession && testResult && (
                <span className={`result-badge ${testResult.passed ? "pass" : "fail"}`}>
                  {testResult.passed ? <Check size={13} /> : <CircleAlert size={13} />}
                  {testResult.passed ? "PASS" : "FAIL"}
                </span>
              )}
            </div>

            {/* ATTEMPT STEPPER / TIMELINE */}
            {repairSession && repairSession.attempts.length > 0 && (
              <div className="attempt-stepper">
                {repairSession.attempts.map((att, idx) => (
                  <button
                    key={att.attempt}
                    className={`attempt-pill ${selectedAttemptIdx === idx ? "active" : ""} ${
                      att.passed ? "pass" : att.status === "ERROR" ? "err" : "fail"
                    }`}
                    onClick={() => setSelectedAttemptIdx(idx)}
                  >
                    <Layers size={13} />
                    <span>Attempt {att.attempt}</span>
                    <small>({att.passed ? "PASS" : att.status === "ERROR" ? "ERR" : "FAIL"})</small>
                  </button>
                ))}
              </div>
            )}

            {/* TABS */}
            <div className="tabs">
              <button className={tab === "diff" ? "selected" : ""} onClick={() => setTab("diff")}>
                <Code2 size={14} /> Diff
              </button>
              <button className={tab === "code" ? "selected" : ""} onClick={() => setTab("code")}>
                <FileCode2 size={14} /> Proposed code
              </button>
              <button className={tab === "tests" ? "selected" : ""} onClick={() => setTab("tests")}>
                <Terminal size={14} /> Test execution logs
              </button>
              {activeAnalysis && (
                <button
                  className={tab === "analysis" ? "selected" : ""}
                  onClick={() => setTab("analysis")}
                  style={{ color: "#f87171" }}
                >
                  <CircleAlert size={14} /> Failure analysis
                </button>
              )}
            </div>

            {/* OUTPUT CONTAINER */}
            <div className="output">
              {!activeDiff && !activeCode && tab !== "tests" && (
                <div className="empty-state">
                  <div className="empty-icon">
                    <Code2 size={25} />
                  </div>
                  <b>Awaiting repair cycle</b>
                  <p>
                    Click &ldquo;Run Autonomous Repair Loop&rdquo; to generate a patch, execute tests in Docker,
                    analyze failures, and iterate until proven.
                  </p>
                  <div className="empty-points">
                    <span>
                      <CheckCircle2 size={14} /> Evidence-gated verification
                    </span>
                    <span>
                      <CheckCircle2 size={14} /> Bounded 3-attempt budget
                    </span>
                    <span>
                      <CheckCircle2 size={14} /> Isolated Docker sandbox
                    </span>
                  </div>
                </div>
              )}

              {/* DIFF VIEW */}
              {tab === "diff" && activeDiff && (
                <pre className="diff-code">
                  {activeDiff.split("\n").map((line, i) => (
                    <div
                      key={i}
                      className={
                        line.startsWith("+") && !line.startsWith("+++")
                          ? "added"
                          : line.startsWith("-") && !line.startsWith("---")
                          ? "removed"
                          : line.startsWith("@@")
                          ? "hunk"
                          : ""
                      }
                    >
                      {line || " "}
                    </div>
                  ))}
                </pre>
              )}

              {/* CODE VIEW */}
              {tab === "code" && activeCode && <pre className="plain-code">{activeCode}</pre>}

              {/* TEST LOGS VIEW */}
              {tab === "tests" && (
                <div className="test-output">
                  {activeAttempt ? (
                    <div className={`test-callout ${activeAttempt.passed ? "pass" : "fail"}`}>
                      {activeAttempt.passed ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}
                      <div>
                        <b>
                          Attempt #{activeAttempt.attempt}:{" "}
                          {activeAttempt.passed ? "Tests Passed in Sandbox" : "Tests Failed"}
                        </b>
                        <small>{activeAttempt.note || "Docker test runner output"}</small>
                      </div>
                    </div>
                  ) : testResult ? (
                    <div className={`test-callout ${testResult.passed ? "pass" : "fail"}`}>
                      {testResult.passed ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}
                      <div>
                        <b>{testResult.passed ? "Command completed successfully" : "Command returned an error"}</b>
                        <small>{testResult.note}</small>
                      </div>
                    </div>
                  ) : (
                    <div className="empty-state">
                      <Terminal size={26} />
                      <b>No test output yet</b>
                      <p>Run the repair loop or execute tests against the proposed patch.</p>
                    </div>
                  )}

                  {(activeStdout || activeStderr) && (
                    <>
                      <label>STDOUT</label>
                      <pre>{activeStdout || "(empty)"}</pre>
                      <label>STDERR</label>
                      <pre>{activeStderr || "(empty)"}</pre>
                      <small className="sandbox-note">
                        Sandbox: Docker container; network disabled; capabilities dropped; memory 512m; cpus 1.
                      </small>
                    </>
                  )}
                </div>
              )}

              {/* FAILURE ANALYSIS VIEW */}
              {tab === "analysis" && activeAnalysis && (
                <div className="failure-analysis-card">
                  <b>
                    <CircleAlert size={16} /> Automated Failure Diagnosis (Fed into next repair iteration)
                  </b>
                  <pre>{activeAnalysis}</pre>
                </div>
              )}
            </div>

            {/* RESULTS FOOTER */}
            {(repairSession || generated) && (
              <div className="result-footer">
                <div className="model-note">
                  <Sparkles size={14} />
                  <span>
                    {repairSession
                      ? `Provider: ${repairSession.model} · ${repairSession.attempts.length} attempts executed · Evidence logged.`
                      : generated?.notes}
                  </span>
                </div>
                <div className="result-actions">
                  <button className="secondary-btn" onClick={applyActiveProposal} disabled={isBusy}>
                    Use in editor buffer
                  </button>
                  <button
                    className="primary-btn small"
                    onClick={runSandboxTests}
                    disabled={isBusy}
                  >
                    {activeAction === "testing" ? (
                      <>
                        <LoaderCircle className="spin" size={15} /> Running…
                      </>
                    ) : (
                      <>
                        <FlaskConical size={15} /> Re-run tests
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>

        <footer className="footer">
          <span>
            PROOFPATCH <b>·</b> EVIDENCE-GATED CODE REPAIR <b>·</b> NEBIUS × NVIDIA GLOBAL AI HACKATHON
          </span>
          <span>
            <ShieldCheck size={13} /> Docker sandbox isolation enforced. Never claims verification without test evidence.
          </span>
        </footer>
      </main>
    </div>
  );
}
