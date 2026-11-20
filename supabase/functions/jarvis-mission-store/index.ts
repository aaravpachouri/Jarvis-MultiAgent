import { createClient } from "npm:@supabase/supabase-js@2.95.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
if (!SUPABASE_URL || !SERVICE_KEY) {
  throw new Error("Required Supabase runtime credentials are unavailable.");
}

const db = createClient(SUPABASE_URL, SERVICE_KEY, {
  auth: { autoRefreshToken: false, persistSession: false },
});
const RESTART_ERROR =
  "Backend restarted before this mission completed. Review its history before rerunning.";
const TERMINAL_STATES = [
  "succeeded",
  "failed",
  "partial",
  "timed_out",
  "cancelled",
  "interrupted",
];
const VALID_STATES = new Set(["queued", "running", ...TERMINAL_STATES]);
const MAX_RECORDS = 264;
const MAX_BODY_BYTES = 512 * 1024;
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function reply(status: number, value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: JSON_HEADERS,
  });
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let i = 0; i < left.length; i += 1) {
    mismatch |= left.charCodeAt(i) ^ right.charCodeAt(i);
  }
  return mismatch === 0;
}

async function authorized(request: Request): Promise<boolean> {
  const header = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([0-9a-f]{64})$/i.exec(header);
  if (!match) return false;

  const { data, error } = await db
    .schema("jarvis_private")
    .from("api_credentials")
    .select("token_sha256")
    .eq("id", 1)
    .maybeSingle();

  if (error || !data?.token_sha256) return false;
  const candidate = await sha256(match[1].toLowerCase());
  return constantTimeEqual(candidate, data.token_sha256);
}

function normalize(row: Record<string, unknown> | null): unknown {
  if (!row) return null;
  return {
    task_id: row.task_id,
    agent_id: row.agent_id,
    status: row.status,
    prompt: row.prompt,
    accepted_at: row.accepted_at,
    started_at: row.started_at,
    finished_at: row.finished_at,
    result: row.result_json ?? null,
    error: row.error ?? null,
  };
}

async function listRecent(limit: number): Promise<unknown[]> {
  const { data, error } = await db
    .schema("jarvis_private")
    .from("jarvis_missions")
    .select(
      "task_id,agent_id,status,prompt,accepted_at,started_at,finished_at,result_json,error,updated_at",
    )
    .order("accepted_at", { ascending: false, nullsFirst: false })
    .order("updated_at", { ascending: false })
    .order("task_id", { ascending: false })
    .limit(limit);
  if (error) throw error;
  return (data ?? []).map((row) => normalize(row));
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") {
    return reply(405, { ok: false, error: "Method not allowed" });
  }

  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (declaredLength > MAX_BODY_BYTES) {
    return reply(413, { ok: false, error: "Request too large" });
  }

  if (!await authorized(request)) {
    return reply(401, { ok: false, error: "Unauthorized" });
  }

  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
      return reply(413, { ok: false, error: "Request too large" });
    }
    const body = JSON.parse(raw);
    const operation = String(body.operation ?? "");

    if (operation === "health") {
      const { error } = await db
        .schema("jarvis_private")
        .from("jarvis_missions")
        .select("task_id")
        .limit(1);
      if (error) throw error;
      return reply(200, { ok: true });
    }

    if (operation === "upsert") {
      const job = body.job ?? {};
      const taskId = String(job.task_id ?? "").trim();
      const agentId = String(job.agent_id ?? "").trim();
      const status = String(job.status ?? "").trim().toLowerCase();
      const prompt = String(job.prompt ?? "").slice(0, 500);
      const resultJson = job.result ?? null;

      if (!taskId || taskId.length > 256 || !agentId ||
          agentId.length > 128 || !VALID_STATES.has(status)) {
        return reply(400, { ok: false, error: "Invalid mission record" });
      }
      if (resultJson !== null &&
          new TextEncoder().encode(JSON.stringify(resultJson)).byteLength > 262144) {
        return reply(413, { ok: false, error: "Mission result too large" });
      }

      const { error } = await db
        .schema("jarvis_private")
        .from("jarvis_missions")
        .upsert({
          task_id: taskId,
          agent_id: agentId,
          status,
          prompt,
          accepted_at: job.accepted_at ?? null,
          started_at: job.started_at ?? null,
          finished_at: job.finished_at ?? null,
          result_json: resultJson,
          error: job.error ? String(job.error).slice(0, 4000) : null,
          updated_at: new Date().toISOString(),
        }, { onConflict: "task_id" });
      if (error) throw error;
      return reply(200, { ok: true });
    }

    if (operation === "get") {
      const taskId = String(body.task_id ?? "").trim();
      if (!taskId || taskId.length > 256) {
        return reply(400, { ok: false, error: "Invalid mission id" });
      }
      const { data, error } = await db
        .schema("jarvis_private")
        .from("jarvis_missions")
        .select("*")
        .eq("task_id", taskId)
        .maybeSingle();
      if (error) throw error;
      return reply(200, { ok: true, record: normalize(data) });
    }

    if (operation === "list") {
      const requested = Number(body.limit ?? MAX_RECORDS);
      const limit = Number.isFinite(requested)
        ? Math.max(0, Math.min(Math.trunc(requested), MAX_RECORDS))
        : MAX_RECORDS;
      return reply(200, { ok: true, records: await listRecent(limit) });
    }

    if (operation === "recover") {
      const { error } = await db
        .schema("jarvis_private")
        .from("jarvis_missions")
        .update({
          status: "interrupted",
          error: RESTART_ERROR,
          updated_at: new Date().toISOString(),
        })
        .in("status", ["queued", "running"]);
      if (error) throw error;
      return reply(200, {
        ok: true,
        records: await listRecent(MAX_RECORDS),
      });
    }

    if (operation === "delete") {
      const taskId = String(body.task_id ?? "").trim();
      if (!taskId || taskId.length > 256) {
        return reply(400, { ok: false, error: "Invalid mission id" });
      }
      const { error } = await db
        .schema("jarvis_private")
        .from("jarvis_missions")
        .delete()
        .eq("task_id", taskId);
      if (error) throw error;
      return reply(200, { ok: true });
    }

    return reply(400, { ok: false, error: "Unknown operation" });
  } catch (error) {
    console.error("JARVIS mission gateway operation failed", {
      name: error instanceof Error ? error.name : "UnknownError",
    });
    return reply(502, { ok: false, error: "Mission storage operation failed" });
  }
});
