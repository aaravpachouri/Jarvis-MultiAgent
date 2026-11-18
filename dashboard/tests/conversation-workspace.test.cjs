const test = require("node:test");
const assert = require("node:assert/strict");
const ui = require("../assets/conversation-ui.js");

test("operator guide renders every declared specialist function, use case and boundary", () => {
  const agent = {
    name: "Quant",
    capabilities: ["trading.market_analysis", "trading.risk"],
    metadata: {
      operator_guide: {
        best_for: ["Read-only market analysis"],
        functions: [
          { capability: "trading.market_analysis", description: "Analyze available market data." },
          { capability: "trading.risk", description: "Assess risk and exposure." }
        ],
        boundaries: ["Never places trades.", "Live quotes require a configured provider."]
      }
    }
  };

  const html = ui.renderOperatorGuide(agent);
  assert.match(html, /FUNCTIONS THIS AGENT CAN PERFORM · 2/);
  assert.match(html, /trading\.market_analysis/);
  assert.match(html, /Analyze available market data/);
  assert.match(html, /BEST USED FOR/);
  assert.match(html, /Never places trades/);
  assert.match(html, /configured provider/);
});

test("operator guide falls back to the live manifest capabilities when guide metadata is absent", () => {
  const html = ui.renderOperatorGuide({ capabilities: ["code.inspect", "code.test"] });
  assert.match(html, /code\.inspect/);
  assert.match(html, /code\.test/);
  assert.match(html, /Declared in the live agent manifest/);
});

test("coordination trace includes nested handoffs and real child outcomes only", () => {
  const events = [
    { type: "agent.task.delegated", task_id: "planner-task", from_agent_id: "voice.jarvis", to_agent_id: "planning.planner", payload: { parent_task_id: "root", capabilities: ["planning.breakdown"] }, timestamp: "2026-11-14T10:00:00Z" },
    { type: "agent.task.result", task_id: "planner-task", from_agent_id: "planning.planner", to_agent_id: "voice.jarvis", payload: { parent_task_id: "root", status: "succeeded", duration_ms: 25 }, timestamp: "2026-11-14T10:00:01Z" },
    { type: "agent.task.delegated", task_id: "research-task", from_agent_id: "voice.jarvis", to_agent_id: "research.athena", payload: { parent_task_id: "planner-task", capabilities: ["research.deep"] }, timestamp: "2026-11-14T10:00:02Z" },
    { type: "task.failed", task_id: "research-task", agent_id: "research.athena", payload: {}, timestamp: "2026-11-14T10:00:03Z" },
    { type: "agent.task.delegated", task_id: "unrelated-task", from_agent_id: "voice.jarvis", to_agent_id: "coding.forge", payload: { parent_task_id: "another-root", capabilities: ["code.write"] }, timestamp: "2026-11-14T10:00:04Z" }
  ];

  const trace = ui.getMissionCoordination(events, "root");
  assert.deepEqual(trace.taskIds.sort(), ["planner-task", "research-task", "root"]);
  assert.equal(trace.handoffs.length, 2);
  assert.equal(trace.handoffs[0].status, "succeeded");
  assert.equal(trace.handoffs[0].capabilities[0], "planning.breakdown");
  assert.equal(trace.handoffs[1].status, "failed");
  assert.equal(trace.handoffs.some(item => item.taskId === "unrelated-task"), false);
});

test("coordination display distinguishes a direct response from a not-yet-observed handoff", () => {
  const trace = { handoffs: [] };
  const direct = ui.renderCoordinationMarkup(trace, {
    agents: [{ agent_id: "research.athena", name: "Athena" }],
    rootAgentId: "research.athena",
    running: false
  });
  assert.match(direct, /handled directly by Athena/);

  const pending = ui.renderCoordinationMarkup(trace, { running: true, rootAgentId: "voice.jarvis" });
  assert.match(pending, /No specialist handoff has been observed yet/);
});

test("operator guide and coordination trace escape untrusted manifest text", () => {
  const guide = ui.renderOperatorGuide({
    capabilities: ["code.inspect"],
    metadata: {
      operator_guide: {
        functions: [{ capability: "code.inspect", description: "<script>alert(1)</script>" }],
        boundaries: ["<img src=x onerror=alert(1)>"]
      }
    }
  });
  assert.doesNotMatch(guide, /<script>/i);
  assert.doesNotMatch(guide, /<img/i);
  assert.match(guide, /&lt;script&gt;/);
});
