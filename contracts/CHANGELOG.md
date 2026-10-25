# Contract changelog and freeze

- Additive shopping payload: `shopping_offer` and `shopping_response` accepted by v2 `market_results`; original PC market payload and wire versions unchanged.

`schema.json` is the source of truth. Minor v2 revisions retain wire `v: 2`; market payloads still use `v: 1`. This inventory records the checked-in contract, not release dates or deployment status.

## Freeze rule

From now on, changes may only add optional fields or new message types. Do not remove or rename fields, make optional fields required, or change existing meanings. Add an entry here for every addition; update schema, TypeScript mirror and examples together through the contract PR process and run `make test-py`. Clients must ignore unknown response fields and unknown message types (and skip unknown draw kinds). Current server request validation remains strict; do not send speculative fields. A breaking change requires a separately negotiated major protocol, outside this freeze.

## v2.9: remember this phone

- Security update: pair codes now issue eight uppercase letters/digits without I/O/0/1. The schema also
  accepts legacy numeric code syntax; expired/unknown codes still cannot join. `pair.path` carries a bound,
  single-use `join` secret instead of the master token. Send `pair` and `join` in the camera WebSocket query;
  its first hello must be `role:camera` with the matching `pair_code`. Wire `v:2` is unchanged.

- Optional `v2_pair_request.kick` (boolean): disconnect the phone paired to this call. A remembered phone then waits for the next call.
- Optional `v2_pair_status.retry_s` (integer): a camera socket may try again after this many seconds (no call yet for the signed-in identity, the call already has a phone, or it ended or disconnected the phone).
- `v2_hello.pair_code` stays optional with role camera: without it, the camera joins the latest live call of the same verified Cloudflare Access identity. A shared token or an open server never qualifies.

## v2.8: local memory across calls

- New `v2_find_item` (server): required `v`, `type:find_item`, `item`, `found`, `count`; optional `turn_id`, `at_ms`, `when`, `place`, `home`, `say`, `live` (`object_id`, `bbox` in the current view), `count_item`, and the `rewind_hit` fields `t_s` (seconds since this call started, negative for an earlier call), `start_s`, `end_s`, `sure`, `alternatives`, `frame_b64` (a small crop of the item, never a full frame) and `objects` (the item's box on that crop).
- New `v2_memory_state` (server): required `v`, `type:memory_state`, `on`, `count`; optional `memory_id`, `crops`, `bytes`, `encrypted`, `retention_days`, `item`, `count_item`.
- Added find_item and memory_state to `server_message_v2`.

## v2.7: Rewind privacy

- Optional `v2_rewind.keep` (boolean, default false): keep this call's TwelveLabs videos after the call; otherwise they are deleted at call end.
- Optional `v2_rewind_state.locked` (boolean): `LOCAL_ONLY=1` on the server, so Rewind cloud cannot be turned on.

## v2.6: Rewind recap

- New `v2_rewind_recap` (server): required `v`, `type:rewind_recap`, `steps` (up to 12 of `{t_s, text}`), `skipped` (typical step names for the mode); optional `turn_id`. Added to `server_message_v2`.

## v2.5: Rewind (call memory through TwelveLabs)

- Optional `v2_hello.rewind` (boolean, default off).
- New `v2_rewind` (client): required `v`, `type:rewind`, `session_id`, `on`. Added to `client_message_v2`.
- New `v2_rewind_state` (server): required `v`, `type:rewind_state`, `on`; optional `clips`, `ready`, `indexing`, `pending`, `notice` (the one-line upload notice).
- New `v2_rewind_hit` (server): required `v`, `type:rewind_hit`, `query`, `t_s`, `start_s`, `end_s`, `sure`; optional `turn_id`, `alternatives` (other call times, seconds), `frame_b64` (the kept frame nearest the hit), `objects` (boxes on that frame, not on the live picture). Times are seconds since the call started.
- Added rewind_state and rewind_hit to `server_message_v2`.

## v2.5: call the expert

- `v2_turn.stage` gains `expert` (the talker escalated; `detail` names the model) and `expert_done` (answer arrived, timed out or cancelled; `detail` says which). Additive: clients ignore unknown stages.

## v2.4: phone as camera

- Optional `v2_hello.role` (`call` default, `camera`) and `v2_hello.pair_code` (4 to 8 digits). A camera socket only streams frames and camera on/off into the call's session; speech and audio stay on the call device.
- New `v2_pair_request` (client, call device) and `v2_pair` (server: `code`, `expires_in_s`, optional `path`). Codes are single-use, expire, and need the same token as the call when token auth is on.
- New `v2_pair_status` (server, both devices): `state` joined, left, camera, rejected or expired; optional `role`, `camera_on`, `source`, `detail`.
- Added pair_request to `client_message_v2`; pair and pair_status to `server_message_v2`.

## v2.3: turn stages, streamed reply text, camera switch

- New `v2_turn` (server): required `v`, `type:turn`, `turn_id`, `stage`, `at_ms`; optional `text`, `detail`, `typical_ms`. Stages: received (the ack, sent at once), transcribing, thinking, backup, speaking, done, failed, cancelled. Every turn ends with exactly one of done, failed or cancelled.
- New `v2_reply_delta` (server): required `v`, `type:reply_delta`, `agent`, `turn_id`, `text`. Spoken reply text as it is generated, for captions only; `v2_say` stays the unit for speech.
- New `v2_camera` (client): required `v`, `type:camera`, `session_id`, `on`. Optional `v2_hello.camera`. Off means voice only: no frame stored, no scene calls.
- Added turn and reply_delta to `server_message_v2`, camera to `client_message_v2`.

## v2.2: server speech input

- Optional `v2_hello.stt`: `browser` or `server`.
- New `v2_audio_in`: required `v`, `type:audio_in`, `session_id`, `seq`, `pcm16_b64`; optional `sample_rate` (16000) and `final`. Input is 16 kHz mono PCM16; final ends an utterance.
- Added `v2_audio_in` to `client_message_v2`. No transcript response type was added.

## v2.1: streaming and tracking

- New `draw_anchor` object (`object_id`, `bbox`); optional `anchor` on every draw-op kind.
- Optional `turn_id`, `part`, `final` on `v2_say` for streamed speech parts.
- New `v2_metrics`: required `v`, `type`; optional `turn_id`, `seq`, `vlm_ms`, `brain_first_token_ms`, `first_audio_ms`, `turn_ms`, `track_ms` (milliseconds).
- New `v2_track`: required `v`, `type`, `seq`, `objects` (scene objects).
- Added metrics and track to `server_message_v2`.

## v2.0: general agent call

- Added `/ws2` vocabulary with wire `v:2`, while keeping `/ws` v1.
- Client messages: `v2_hello` (pack, optional board/client/TTS choice), `v2_frame`, `v2_user_text` (final and optional source), `v2_barge_in`, `v2_confirm`.
- Server messages: `v2_draw`, `v2_say`, `v2_audio` (optional sample rate and final chunk flag), `v2_agent_state` (optional focus), `v2_market_results`, `v2_error`; client/server v2 message unions.
- Draw kinds: box, highlight, circle, arrow, path, label, pin, clear. Required IDs; optional role, label and TTL. Coordinates normalized to the sent frame; bbox is top-left `[x,y,w,h]`.
- Agent IDs helper/shopper; packs general/pc_build/cooking/assemble; presence idle/listening/thinking/speaking; draw roles target/info/warn/ok.
- `scene_object` (`id`, `label`, `bbox`, `conf`), `scene_request` (`image_b64`, optional queries/max_objects), `scene_response` (caption/objects/ms, optional source).
- `brain_turn`: internal agent/say/draw intent with optional market_category; object-ID targets resolved by the server. Not a phone wire message.
- V2 errors: bad_message, power_off_required, refused, vision_unavailable, brain_unavailable, internal.

## v1: PC build flow and service boundaries

- `/ws` client messages: hello, project_open, frame, photo, confirm, user_text, parts_scan, market, audio.
- `/ws` server messages: overlay, say, step, verdict, parts, market_results, summary, annotated, error, audio. Client/server message unions.
- Step states SAFETY, IDENTIFY, LOCATE, GUIDE, AWAIT, VERIFY, DONE, RETRY; closed allowed-part set and power-off confirmation.
- Vision request/response pairs: identify, locate, verify; health_response; normalized boxes, confidence and pin geometry.
- Voice adapter messages: voice_say, voice_transcript, voice_barge_in, voice_spoken; 16 kHz mono PCM16 socket audio.
- Market definitions: part_category, part_specs, part, market_query, compat, offer, market_response. Compatibility is fits/no/unverified; unknown facts remain unverified.
- Header-table definition for checked board/header/pin mappings.

## Integration note (new tonight)

Token authentication, `/api/scene`, `/api/market`, `/api/status`, mock mode and HTTPS helper commands are parallel server additions. They do not change the v2 wire integer. REST scene/market reuse existing contracts; no status contract exists in this checkout. Their described behavior is in [the phone reference](../docs/api.md), pending integration verification.
