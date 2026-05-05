// POST /api/events/push
// Desktop pushes one audit event. Phone reads them via /api/events/list.
//
// Headers: x-admin-token: <token>
// Body:   { instance_id, event: { ... } }
//
// Stored as `evt:{instance_id}:{ts_ms}:{rand4}` with 1h TTL — plenty for a phone
// that opens the app every few minutes.

import { json, err, preflight, readJson, authAdmin, touchInstance, TTL_EVENT } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestPost({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

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
