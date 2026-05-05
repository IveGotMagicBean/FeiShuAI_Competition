// POST /api/approvals/decide
// Phone presses Approve/Deny. We:
//   1. Mark the approval `_state` = "approved"|"denied" so list stops returning it as pending
//   2. Enqueue a decision under `dec:{instance_id}:{pid}` for the desktop to poll
//
// Headers: x-mobile-token
// Body:    { id, approved: bool, by?: "phone" }

import { json, err, preflight, readJson, authMobile, TTL_APPROVAL, TTL_DECISION } from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestPost({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing");

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
