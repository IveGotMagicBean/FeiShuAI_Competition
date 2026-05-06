// Sentinel-MCP · Cloudflare Worker (Workers + Static Assets 模式)
// ==============================================================
//
// 单一 entry：
//   - /api/*  → router 派发到对应的 handler
//   - 其他    → 透传给 ASSETS binding（serve website/ 下的静态文件）
//
// Bindings (在 Cloudflare 控制台 + wrangler.toml 中配置):
//   PAIR_KV  - KV namespace，存配对码 / 实例 / 事件 / 审批 / 决策
//   ASSETS   - 静态资源 binding，指向 website/
//
// 这是从原 Pages Functions (website/functions/api/...) 移植过来的
// —— 因为这个项目是 Cloudflare Workers（不是 Pages），`functions/` 目录不会被自动识别。

// ============================================================
// KV schema
// ============================================================
//
// pair:{code}               → instance_id           TTL 5 min
// inst:{instance_id}        → { admin_token, mobile_token, created_at, last_seen }
// mtok:{token}              → instance_id           TTL 30 days  (mobile token reverse index)
// evt:{instance_id}:{ts}    → event JSON            TTL 1 hour
// apr:{instance_id}:{pid}   → approval JSON         TTL 6 hours
// dec:{instance_id}:{pid}   → decision JSON         TTL 1 day    (queue: desktop polls + deletes)

const TTL_PAIR_CODE = 5 * 60;
const TTL_EVENT = 60 * 60;
const TTL_APPROVAL = 6 * 60 * 60;
const TTL_DECISION = 24 * 60 * 60;
const TTL_INSTANCE = 30 * 24 * 60 * 60;

// ============================================================
// Helpers
// ============================================================

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type, x-admin-token, x-mobile-token",
  "access-control-max-age": "86400",
};

function json(data, init = {}) {
  return new Response(JSON.stringify(data), {
    status: init.status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...CORS,
      ...(init.headers || {}),
    },
  });
}

function err(status, message) {
  return json({ error: message }, { status });
}

function preflight() {
  return new Response(null, { status: 204, headers: CORS });
}

function newPairCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const buf = new Uint8Array(6);
  crypto.getRandomValues(buf);
  let out = "";
  for (let i = 0; i < 6; i++) out += alphabet[buf[i] % alphabet.length];
  return out;
}

function newToken() {
  const buf = new Uint8Array(32);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function newInstanceId() {
  return crypto.randomUUID();
}

async function readJson(request) {
  try {
    const ct = request.headers.get("content-type") || "";
    if (!ct.includes("json")) return null;
    return await request.json();
  } catch {
    return null;
  }
}

async function authAdmin(env, instance_id, admin_token) {
  if (!instance_id || !admin_token) return null;
  const raw = await env.PAIR_KV.get(`inst:${instance_id}`);
  if (!raw) return null;
  let inst;
  try { inst = JSON.parse(raw); } catch { return null; }
  if (inst.admin_token !== admin_token) return null;
  return inst;
}

async function authMobile(env, mobile_token) {
  if (!mobile_token) return null;
  const instance_id = await env.PAIR_KV.get(`mtok:${mobile_token}`);
  if (!instance_id) return null;
  const raw = await env.PAIR_KV.get(`inst:${instance_id}`);
  if (!raw) return null;
  let inst;
  try { inst = JSON.parse(raw); } catch { return null; }
  if (inst.mobile_token !== mobile_token) return null;
  return { instance_id, instance: inst };
}

async function touchInstance(env, instance_id, inst) {
  inst.last_seen = Date.now();
  await env.PAIR_KV.put(`inst:${instance_id}`, JSON.stringify(inst), {
    expirationTtl: TTL_INSTANCE,
  });
}

// ============================================================
// Handlers
// ============================================================

// POST /api/pair/register — desktop 启动时调一次，拿配对码
async function pairRegister(request, env) {
  const body = (await readJson(request)) || {};
  let instance_id = body.instance_id;
  let admin_token = body.admin_token;
  let inst = null;

  if (instance_id && admin_token) {
    inst = await authAdmin(env, instance_id, admin_token);
    if (!inst) return err(401, "invalid instance_id or admin_token");
  } else {
    instance_id = newInstanceId();
    admin_token = newToken();
    inst = {
      admin_token,
      mobile_token: null,
      created_at: Date.now(),
      last_seen: Date.now(),
    };
    await env.PAIR_KV.put(`inst:${instance_id}`, JSON.stringify(inst), {
      expirationTtl: TTL_INSTANCE,
    });
  }

  const pair_code = newPairCode();
  await env.PAIR_KV.put(`pair:${pair_code}`, instance_id, {
    expirationTtl: TTL_PAIR_CODE,
  });

  await touchInstance(env, instance_id, inst);

  return json({
    instance_id,
    admin_token,
    pair_code,
    pair_code_expires_at: Date.now() + TTL_PAIR_CODE * 1000,
  });
}

// POST /api/pair/redeem — phone 输入 6 位码换 mobile_token
async function pairRedeem(request, env) {
  const body = (await readJson(request)) || {};
  const code = String(body.code || "").trim().toUpperCase();
  if (!/^[A-Z2-9]{6}$/.test(code)) return err(400, "invalid code format (expected 6 chars A-Z 2-9)");

  const instance_id = await env.PAIR_KV.get(`pair:${code}`);
  if (!instance_id) return err(404, "code expired or not found");

  await env.PAIR_KV.delete(`pair:${code}`);

  const raw = await env.PAIR_KV.get(`inst:${instance_id}`);
  if (!raw) return err(410, "instance expired");
  const inst = JSON.parse(raw);

  if (inst.mobile_token) {
    await env.PAIR_KV.delete(`mtok:${inst.mobile_token}`);
  }
  const mobile_token = newToken();
  inst.mobile_token = mobile_token;
  inst.last_seen = Date.now();

  await env.PAIR_KV.put(`inst:${instance_id}`, JSON.stringify(inst), {
    expirationTtl: TTL_INSTANCE,
  });
  await env.PAIR_KV.put(`mtok:${mobile_token}`, instance_id, {
    expirationTtl: TTL_INSTANCE,
  });

  return json({ instance_id, mobile_token });
}

// POST /api/events/push — desktop push 一条审计事件
async function eventsPush(request, env) {
  const admin_token = request.headers.get("x-admin-token") || "";
  const body = (await readJson(request)) || {};
  const { instance_id, event } = body;
  if (!instance_id || !event) return err(400, "missing instance_id or event");

  const inst = await authAdmin(env, instance_id, admin_token);
  if (!inst) return err(401, "auth failed");

  const ts = Date.now();
  const rand4 = Math.floor(Math.random() * 0xffff).toString(16).padStart(4, "0");
  const key = `evt:${instance_id}:${ts.toString().padStart(13, "0")}:${rand4}`;

  await env.PAIR_KV.put(key, JSON.stringify({ ...event, _ts: ts }), {
    expirationTtl: TTL_EVENT,
  });
  await touchInstance(env, instance_id, inst);

  return json({ ok: true, key });
}

// GET /api/events/list?since=<ts>&limit=<n> — phone 增量拉事件
async function eventsList(request, env) {
  const mobile_token = request.headers.get("x-mobile-token") || "";
  const auth = await authMobile(env, mobile_token);
  if (!auth) return err(401, "auth failed");

  const url = new URL(request.url);
  const since = parseInt(url.searchParams.get("since") || "0", 10);
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), 200);

  const prefix = `evt:${auth.instance_id}:`;
  const startKey = since > 0
    ? `${prefix}${(since + 1).toString().padStart(13, "0")}`
    : prefix;

  const listing = await env.PAIR_KV.list({ prefix, start: startKey, limit });
  const events = [];
  let next_since = since;

  for (const k of listing.keys) {
    const val = await env.PAIR_KV.get(k.name);
    if (!val) continue;
    try {
      const ev = JSON.parse(val);
      events.push(ev);
      if (ev._ts && ev._ts > next_since) next_since = ev._ts;
    } catch { /* skip */ }
  }

  return json({ events, count: events.length, next_since });
}

// POST /api/approvals/push — desktop push 审批请求
async function approvalsPush(request, env) {
  const admin_token = request.headers.get("x-admin-token") || "";
  const body = (await readJson(request)) || {};
  const { instance_id, approval } = body;
  if (!instance_id || !approval || !approval.id) {
    return err(400, "missing instance_id or approval (with .id)");
  }
  const inst = await authAdmin(env, instance_id, admin_token);
  if (!inst) return err(401, "auth failed");

  const stored = { ...approval, _ts: Date.now(), _state: "pending" };
  await env.PAIR_KV.put(
    `apr:${instance_id}:${approval.id}`,
    JSON.stringify(stored),
    { expirationTtl: TTL_APPROVAL },
  );
  await touchInstance(env, instance_id, inst);

  return json({ ok: true, id: approval.id });
}

// GET /api/approvals/list?only_pending=1 — phone 拉审批列表
async function approvalsList(request, env) {
  const mobile_token = request.headers.get("x-mobile-token") || "";
  const auth = await authMobile(env, mobile_token);
  if (!auth) return err(401, "auth failed");

  const url = new URL(request.url);
  const only_pending = url.searchParams.get("only_pending") !== "0";

  const prefix = `apr:${auth.instance_id}:`;
  const listing = await env.PAIR_KV.list({ prefix, limit: 100 });
  const approvals = [];
  for (const k of listing.keys) {
    const val = await env.PAIR_KV.get(k.name);
    if (!val) continue;
    try {
      const a = JSON.parse(val);
      if (only_pending && a._state !== "pending") continue;
      approvals.push(a);
    } catch { /* skip */ }
  }
  approvals.sort((a, b) => (b._ts || 0) - (a._ts || 0));

  return json({ approvals, count: approvals.length });
}

// POST /api/approvals/decide — phone 批/拒
async function approvalsDecide(request, env) {
  const mobile_token = request.headers.get("x-mobile-token") || "";
  const auth = await authMobile(env, mobile_token);
  if (!auth) return err(401, "auth failed");

  const body = (await readJson(request)) || {};
  const id = body.id;
  if (!id) return err(400, "missing id");
  if (typeof body.approved !== "boolean") return err(400, "missing 'approved' boolean");

  const aprKey = `apr:${auth.instance_id}:${id}`;
  const aprRaw = await env.PAIR_KV.get(aprKey);
  if (!aprRaw) return err(404, "approval not found or expired");

  const apr = JSON.parse(aprRaw);
  if (apr._state && apr._state !== "pending") {
    return err(409, `already ${apr._state}`);
  }

  apr._state = body.approved ? "approved" : "denied";
  apr._decided_at = Date.now();
  apr._decided_by = String(body.by || "phone");
  await env.PAIR_KV.put(aprKey, JSON.stringify(apr), { expirationTtl: TTL_APPROVAL });

  const decision = {
    id,
    approved: body.approved,
    by: apr._decided_by,
    ts: apr._decided_at,
  };
  await env.PAIR_KV.put(
    `dec:${auth.instance_id}:${id}`,
    JSON.stringify(decision),
    { expirationTtl: TTL_DECISION },
  );

  return json({ ok: true, id, approved: body.approved });
}

// GET /api/decisions/poll?instance_id=...&consume=1 — desktop 轮询并消费决策
async function decisionsPoll(request, env) {
  const admin_token = request.headers.get("x-admin-token") || "";
  const url = new URL(request.url);
  const instance_id = url.searchParams.get("instance_id") || "";
  const consume = url.searchParams.get("consume") !== "0";

  const inst = await authAdmin(env, instance_id, admin_token);
  if (!inst) return err(401, "auth failed");

  const prefix = `dec:${instance_id}:`;
  const listing = await env.PAIR_KV.list({ prefix, limit: 100 });
  const decisions = [];

  for (const k of listing.keys) {
    const val = await env.PAIR_KV.get(k.name);
    if (!val) continue;
    try {
      decisions.push(JSON.parse(val));
      if (consume) await env.PAIR_KV.delete(k.name);
    } catch { /* skip */ }
  }
  await touchInstance(env, instance_id, inst);

  return json({ decisions, count: decisions.length });
}

// ============================================================
// Router
// ============================================================

const routes = {
  "POST /api/pair/register":    pairRegister,
  "POST /api/pair/redeem":      pairRedeem,
  "POST /api/events/push":      eventsPush,
  "GET /api/events/list":       eventsList,
  "POST /api/approvals/push":   approvalsPush,
  "GET /api/approvals/list":    approvalsList,
  "POST /api/approvals/decide": approvalsDecide,
  "GET /api/decisions/poll":    decisionsPoll,
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // /api/* → router 派发
    if (path.startsWith("/api/")) {
      // CORS preflight
      if (request.method === "OPTIONS") return preflight();

      // KV binding 必须存在
      if (!env.PAIR_KV) {
        return err(500, "PAIR_KV binding missing — bind a KV namespace named PAIR_KV in worker settings");
      }

      const handler = routes[`${request.method} ${path}`];
      if (!handler) return err(404, `no route for ${request.method} ${path}`);

      try {
        return await handler(request, env);
      } catch (e) {
        return err(500, `handler error: ${e.message}`);
      }
    }

    // 其他全部走静态资源
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }
    return err(500, "ASSETS binding missing — set assets.directory in wrangler.toml");
  },
};
