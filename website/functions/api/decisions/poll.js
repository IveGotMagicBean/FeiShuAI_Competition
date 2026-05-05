// GET /api/decisions/poll?consume=1
// Desktop long-polls for decisions made on the phone, applies them to the local
// approvals table, then deletes the queue entries (consume=1, default).
//
// Headers: x-admin-token
// Query:   instance_id, consume (default 1)
// Returns: { decisions: [{ id, approved, by, ts }, ...] }

import { json, err, preflight, authAdmin, touchInstance } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestGet({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

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
