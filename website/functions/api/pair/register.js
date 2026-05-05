// POST /api/pair/register
// Desktop boot-time call: get a fresh 6-char pair code that the user can type into
// their phone. The code maps to an instance_id (created if missing) and expires in 5min.
//
// Body: { instance_id?: string, admin_token?: string }
//   - First time:  client sends nothing, we mint instance_id + admin_token, return both.
//                  Client persists them locally and reuses on every restart.
//   - Subsequent:  client sends its existing instance_id+admin_token, we just rotate the
//                  pair code (so they can re-pair another phone any time).
//
// Response: { instance_id, admin_token, pair_code, pair_code_expires_at }

import {
  json, err, preflight, readJson,
  newPairCode, newToken, newInstanceId,
  authAdmin, touchInstance,
  TTL_PAIR_CODE, TTL_INSTANCE,
} from "../../_lib.js";

export const onRequestOptions = preflight;

export async function onRequestPost({ request, env }) {
  if (!env.PAIR_KV) return err(500, "PAIR_KV binding missing — bind a KV namespace named PAIR_KV in Pages settings");

  const body = (await readJson(request)) || {};
  let instance_id = body.instance_id;
  let admin_token = body.admin_token;
  let inst = null;

  if (instance_id && admin_token) {
    inst = await authAdmin(env, instance_id, admin_token);
    if (!inst) return err(401, "invalid instance_id or admin_token");
  } else {
    // First-time registration.
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

  // Rotate the pair code (5 min one-shot).
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
