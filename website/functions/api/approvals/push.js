// POST /api/approvals/push
// Desktop pushes a pending approval request (ASK_USER outcome).
//
// Headers: x-admin-token
// Body:    { instance_id, approval: { id, tool_name, args, risk_score, reason, ... } }

import { json, err, preflight, readJson, authAdmin, touchInstance, TTL_APPROVAL } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestPost({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

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
