// website/functions/_lib.js
// Shared helpers for Sentinel-MCP Cloudflare Pages Functions.
//
// Storage model (Cloudflare KV binding `PAIR_KV`):
//   pair:{code}              → instance_id          TTL 5 min   (one-shot redemption)
//   inst:{instance_id}       → { admin_token, mobile_token, created_at, last_seen }
//   evt:{instance_id}:{ts}   → event JSON           TTL 1 hour
//   apr:{instance_id}:{pid}  → approval JSON        TTL 6 hours
//   dec:{instance_id}:{pid}  → decision JSON        TTL 1 day   (queue: desktop polls + deletes)
//
// Two tokens per instance:
//   admin_token  — desktop holds; needed for /events/push, /approvals/push, /decisions/poll
//   mobile_token — phone holds (after redeeming a pair code); needed for /events/list,
//                  /approvals/list, /approvals/decide
//
// CORS: not strictly needed since same-origin (.apk and Pages share host), but enabled
// permissively for browser-side dev/testing.

export const TTL_PAIR_CODE   = 5 * 60;            // 5 minutes
export const TTL_EVENT       = 60 * 60;           // 1 hour
export const TTL_APPROVAL    = 6 * 60 * 60;       // 6 hours
export const TTL_DECISION    = 24 * 60 * 60;      // 1 day
export const TTL_INSTANCE    = 30 * 24 * 60 * 60; // 30 days (refreshed on each push)

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type, x-admin-token, x-mobile-token",
  "access-control-max-age": "86400",
};

export function json(data, init = {}) {
  return new Response(JSON.stringify(data), {
    status: init.status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...CORS,
      ...(init.headers || {}),
    },
  });
}

export function err(status, message) {
  return json({ error: message }, { status });
}

export function preflight() {
  return new Response(null, { status: 204, headers: CORS });
}

// 6-char alphanumeric code, no easily confused chars (no 0/O/1/I).
export function newPairCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const buf = new Uint8Array(6);
  crypto.getRandomValues(buf);
  let out = "";
  for (let i = 0; i < 6; i++) out += alphabet[buf[i] % alphabet.length];
  return out;
}

// 32-byte random hex (cryptographically random).
export function newToken() {
  const buf = new Uint8Array(32);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function newInstanceId() {
  // RFC 4122 v4-ish UUID, plenty for our purpose.
  return crypto.randomUUID();
}

export async function readJson(request) {
  try {
    const ct = request.headers.get("content-type") || "";
    if (!ct.includes("json")) return null;
    return await request.json();
  } catch {
    return null;
  }
}

// Verify an admin_token belongs to a given instance_id. Returns the instance record on success.
export async function authAdmin(env, instance_id, admin_token) {
  if (!instance_id || !admin_token) return null;
  const raw = await env.PAIR_KV.get(`inst:${instance_id}`);
  if (!raw) return null;
  let inst;
  try { inst = JSON.parse(raw); } catch { return null; }
  if (inst.admin_token !== admin_token) return null;
  return inst;
}

// Verify a mobile_token. Returns { instance_id, instance } on success.
// We index by `mtok:{token}` → instance_id for O(1) lookup.
export async function authMobile(env, mobile_token) {
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

export async function touchInstance(env, instance_id, inst) {
  inst.last_seen = Date.now();
  await env.PAIR_KV.put(`inst:${instance_id}`, JSON.stringify(inst), {
    expirationTtl: TTL_INSTANCE,
  });
}
