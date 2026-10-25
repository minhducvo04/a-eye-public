// A-eye contracts v1, hand-written mirror of contracts/schema.json.
// schema.json is the source of truth. Change both in the same PR.
// Coordinates are normalized 0..1 of the sent frame. BBox is [x, y, w, h] from top-left.

export type V = 1;
export type Unit = number;
export type BBox = [Unit, Unit, Unit, Unit];
export type Point = [Unit, Unit];

export type Item =
  | "cpu_fan_cable" | "sys_fan_cable" | "f_panel_bundle" | "ram_stick"
  | "sata_cable" | "m2_ssd" | "atx_24pin" | "eps_8pin";
export const ALLOWED_PARTS: Item[] = [
  "cpu_fan_cable", "sys_fan_cable", "f_panel_bundle", "ram_stick",
  "sata_cable", "m2_ssd", "atx_24pin", "eps_8pin",
];
export type StepState = "SAFETY" | "IDENTIFY" | "LOCATE" | "GUIDE" | "AWAIT" | "VERIFY" | "DONE" | "RETRY";
export type PartCategory = "cpu" | "board" | "ram" | "ssd" | "gpu" | "psu" | "cooler" | "case" | "fan";

// ---- client -> server (WS /ws) ----
export interface Hello { v: V; type: "hello"; session_id: string; board_id?: string; mode: "live" | "photo"; client?: "web" | "ios" }
export interface ProjectOpen { v: V; type: "project_open"; session_id: string; project_id: string }
export interface Frame {
  v: V; type: "frame"; session_id: string; seq: number; ts_ms: number; w: number; h: number;
  orientation?: "portrait" | "landscape_left" | "landscape_right" | "portrait_upside_down"; jpeg_b64: string;
}
export interface Photo { v: V; type: "photo"; session_id: string; seq: number; w: number; h: number; jpeg_b64: string }
export interface Confirm { v: V; type: "confirm"; session_id: string; what: "power_off" | "seated" | "check_again" | "next" | "stop"; value: boolean }
export interface UserText { v: V; type: "user_text"; session_id: string; text: string }
export interface PartsScan { v: V; type: "parts_scan"; session_id: string; jpeg_b64: string }
export interface MarketAsk { v: V; type: "market"; session_id: string; category: PartCategory; budget_usd?: number }
export interface Audio { v: V; type: "audio"; session_id: string; utterance_id?: string; sample_rate?: 16000; pcm16_b64: string }
export type ClientMessage = Hello | ProjectOpen | Frame | Photo | Confirm | UserText | PartsScan | MarketAsk | Audio;

// ---- server -> client ----
export interface Pin { n: number; x: Unit; y: Unit; role: "target" | "other" }
export interface Box { id: string; header?: string; label?: string; bbox: BBox; role: "target" | "other" | "wrong" | "ok"; conf: number; pins?: Pin[] }
export interface Overlay {
  v: V; type: "overlay"; seq: number; level: "header" | "pin"; boxes: Box[];
  arrow?: { from: Point; to: Point };
  diagram?: { header: string; img: string; page?: number; rotation_deg?: number; highlight_pins?: number[] };
}
export interface Say { v: V; type: "say"; utterance_id: string; step_id?: string; text: string }
export interface Step { v: V; type: "step"; step_id: string; state: StepState; item?: Item; target_header?: string; index?: number; total?: number }
export interface Verdict { v: V; type: "verdict"; step_id: string; pass: boolean; abstain?: boolean; conf: number; reason: string; offset_pins?: number }
export interface Parts { v: V; type: "parts"; items: Part[] }
export interface MarketResults { v: V; type: "market_results"; result: MarketResponse }
export interface Summary { v: V; type: "summary"; text: string; done?: string[]; failed?: string[]; to_buy?: string[] }
export interface Annotated { v: V; type: "annotated"; seq: number; png_b64: string }
export interface ErrorMsg {
  v: V; type: "error";
  code: "bad_message" | "power_off_required" | "refused_part" | "vision_unavailable" | "stale_frame" | "internal";
  message: string;
}
export type ServerMessage = Overlay | Say | Step | Verdict | Parts | MarketResults | Summary | Annotated | ErrorMsg | Audio;

// ---- server -> vision (HTTP JSON) ----
export interface IdentifyRequest { image_b64: string; candidates: Item[] }
export interface IdentifyResponse { item: Item | "unknown"; conf: number; bbox?: BBox; ms: number }
export interface LocateRequest { image_b64: string; board_id: string; header: string; want_pins?: number[] }
export interface LocateResponse {
  found: boolean; bbox?: BBox; source?: "ocr" | "landmarks" | "yolo" | "fake"; conf: number; blur?: boolean; glare?: boolean;
  others?: { header: string; bbox: BBox; conf: number }[];
  pins?: { grid: Point[]; pin1: Point; residual: number; keyed_gap: boolean; pin_ok: boolean };
  ms: number;
}
export interface VerifyRequest { image_b64: string; board_id: string; header: string; expect: { item: Item; pins?: number[] } }
export interface VerifyResponse { pass: boolean; abstain?: boolean; conf: number; offset_pins?: number; seen_header?: string; reason: string; ms: number }
export interface HealthResponse { ok: boolean; models: string[]; gpu?: string; fake?: boolean }

// ---- voice adapter <-> orchestrator ----
export interface VoiceSay { type: "say"; utterance_id: string; text: string }
export interface VoiceTranscript { type: "transcript"; text: string; final: boolean; ts_ms: number }
export interface VoiceBargeIn { type: "barge_in"; utterance_id: string; at_ms: number }
export interface VoiceSpoken { type: "spoken"; utterance_id: string }

// ---- market ----
export interface PartSpecs {
  socket?: string; ddr_gen?: "DDR4" | "DDR5"; dimm_format?: "DIMM" | "SODIMM"; ecc?: boolean; buffered?: boolean;
  speed_mts?: number; max_speed_mts?: number; capacity_gb?: number; max_capacity_gb?: number; dimm_slots?: number; modules?: number;
  form_factor?: string; tdp_w?: number; wattage_w?: number; height_mm?: number; cooler_clearance_mm?: number; pcie_gen?: number; m2_key?: string;
}
export interface Part { id?: string; category: PartCategory; brand?: string; model: string; specs: PartSpecs; source?: "scan" | "typed" | "fixture"; confirmed: boolean }
export interface MarketQuery { v: V; category: PartCategory; owned_parts: Part[]; budget_usd?: number; max_results?: number }
export interface Compat { status: "fits" | "no" | "unverified"; reasons: string[]; unknown: string[] }
export interface Offer {
  title: string; retailer: string; price_usd: number; shipping_usd?: number; condition?: "new" | "used" | "refurbished";
  url: string; specs?: PartSpecs; source: "apify" | "brightdata" | "cache" | "fixture"; fetched_at: string; compat: Compat; explain?: string;
}
export interface MarketResponse { v: V; query_key: string; category: PartCategory; items: Offer[]; note?: string }

// =====================================================================
// v2: multi-purpose "see, point, draw, talk" agent on WS /ws2. v1 above stays supported on /ws.
// =====================================================================
export type V2 = 2;
export type AgentId = "helper" | "shopper";
export type PackId = "general" | "pc_build" | "cooking" | "assemble" | "memory" | "home";
export type PresenceState = "idle" | "listening" | "thinking" | "speaking";
export type DrawRole = "target" | "info" | "warn" | "ok";

interface OpBase { id: string; role?: DrawRole; label?: string; ttl_ms?: number; anchor?: { object_id: string; bbox: BBox } }
export type DrawOp =
  | (OpBase & { kind: "box"; bbox: BBox })
  | (OpBase & { kind: "highlight"; bbox: BBox })
  | (OpBase & { kind: "circle"; center: Point; r: Unit })
  | (OpBase & { kind: "arrow"; from: Point; to: Point })
  | (OpBase & { kind: "path"; points: Point[] })
  | (OpBase & { kind: "label"; at: Point; text: string })
  | (OpBase & { kind: "pin"; at: Point })
  | (OpBase & { kind: "clear" });

// client -> server
export interface V2Hello { v: V2; type: "hello"; session_id: string; pack: PackId; board_id?: string; client?: "web" | "ios"; tts?: "server" | "browser"; stt?: "browser" | "server"; voice?: string; private?: boolean; cloud?: { claude?: boolean; cartesia?: boolean; twelvelabs?: boolean } }
export interface V2Frame { v: V2; type: "frame"; session_id: string; seq: number; ts_ms: number; w: number; h: number; jpeg_b64: string }
export interface V2UserText { v: V2; type: "user_text"; session_id: string; text: string; final: boolean; source?: "browser_stt" | "whisper" | "typed" }
export interface V2BargeIn { v: V2; type: "barge_in"; session_id: string; utterance_id: string; at_ms: number }
export interface V2Confirm { v: V2; type: "confirm"; session_id: string; what: "power_off" | "seated" | "check_again" | "next" | "stop"; value: boolean }
export type ClientMessageV2 = V2Hello | V2Frame | V2UserText | V2BargeIn | V2Confirm | V2AudioIn;

// server -> client
export interface V2Draw { v: V2; type: "draw"; agent: AgentId; seq: number; ops: DrawOp[] }
export interface V2Say { v: V2; type: "say"; agent: AgentId; utterance_id: string; text: string; turn_id?: string; part?: number; final?: boolean }
export interface V2Audio { v: V2; type: "audio"; agent: AgentId; utterance_id: string; sample_rate?: 16000; pcm16_b64: string; final?: boolean }
export interface V2AgentState { v: V2; type: "agent_state"; agent: AgentId; state: PresenceState; focus?: Point }
export interface V2MarketResults { v: V2; type: "market_results"; agent: "shopper"; result: MarketResponse | ShoppingResponse }
export interface V2Error { v: V2; type: "error"; code: "bad_message" | "power_off_required" | "refused" | "vision_unavailable" | "brain_unavailable" | "internal"; message: string }
export type ServerMessageV2 = V2Draw | V2Say | V2Audio | V2AgentState | V2MarketResults | V2Error | V2Metrics | V2Track;

// server -> vision: POST /scene
export interface SceneObject { id: string; label: string; bbox: BBox; conf: number }
export interface SceneRequest { image_b64: string; queries?: string[]; max_objects?: number }
export interface SceneResponse { caption: string; objects: SceneObject[]; source?: "ollama" | "claude" | "fake"; ms: number }

// v2.1 (additive, real time): anchored ops, streamed say parts, dev metrics, fast-loop track updates.
export interface DrawAnchor { object_id: string; bbox: BBox }
// Every DrawOp may carry: anchor?: DrawAnchor  (the client moves the op with its tracked region)
export interface V2SayStreamFields { turn_id?: string; part?: number; final?: boolean }
export interface V2Metrics { v: V2; type: "metrics"; turn_id?: string; seq?: number; vlm_ms?: number; brain_first_token_ms?: number; first_audio_ms?: number; turn_ms?: number; track_ms?: number; stt_ms?: number; tts_ms?: number }
export interface V2Track { v: V2; type: "track"; seq: number; objects: SceneObject[] }

// v2.2 (additive): server speech to text. V2Hello may carry stt?: "browser" | "server".
export interface V2AudioIn { v: V2; type: "audio_in"; session_id: string; seq: number; sample_rate?: 16000; pcm16_b64: string; final?: boolean }

// v2.3 (additive): turn stages (ack), streamed reply text, camera on/off. V2Hello may carry camera?: boolean.
export interface V2Turn { v: V2; type: "turn"; turn_id: string; stage: "received" | "transcribing" | "thinking" | "backup" | "speaking" | "done" | "failed" | "cancelled" | "expert" | "expert_done"; at_ms: number; text?: string; detail?: string; typical_ms?: number }
export interface V2ReplyDelta { v: V2; type: "reply_delta"; agent: AgentId; turn_id: string; text: string }
export interface V2Camera { v: V2; type: "camera"; session_id: string; on: boolean }

// v2.4 (additive): phone as camera. V2Hello may carry role?: "call" | "camera" and pair_code?: string (new codes: 8-12 uppercase letters/digits without I/O/0/1; legacy numeric syntax accepted).
export interface V2PairRequest { v: V2; type: "pair_request"; session_id: string; kick?: boolean }
export interface V2Pair { v: V2; type: "pair"; code: string; expires_in_s: number; path?: string }
export interface V2PairStatus { v: V2; type: "pair_status"; state: "joined" | "left" | "camera" | "rejected" | "expired"; role?: "call" | "camera"; camera_on?: boolean; source?: "call" | "camera"; detail?: string; retry_s?: number }

// v2.5 (additive): Rewind, a searchable memory of the call (TwelveLabs). V2Hello may carry rewind?: boolean.
// V2Rewind joins the client v2 union; V2RewindState and V2RewindHit join the server v2 union.
export interface V2Rewind { v: V2; type: "rewind"; session_id: string; on: boolean }
export interface V2RewindState { v: V2; type: "rewind_state"; on: boolean; clips?: number; ready?: number; indexing?: number; pending?: number; notice?: string }
export interface V2RewindHit { v: V2; type: "rewind_hit"; turn_id?: string; query: string; t_s: number; start_s: number; end_s: number; sure: boolean; alternatives?: number[]; frame_b64?: string; objects?: SceneObject[] }

// v2.6 (additive): Rewind recap. V2RewindRecap joins the server v2 union.
export interface V2RewindRecap { v: V2; type: "rewind_recap"; turn_id?: string; steps: { t_s: number; text: string }[]; skipped: string[] }

// v2.7 (additive): Rewind privacy. V2Rewind may carry keep?: boolean; V2RewindState may carry locked?: boolean.

// v2.8 (additive): local memory across calls. Both join the server v2 union.
export interface V2FindItem { v: V2; type: "find_item"; turn_id?: string; item: string; found: boolean; at_ms?: number; when?: string; place?: string; home?: string; say?: string; t_s?: number; start_s?: number; end_s?: number; sure?: boolean; alternatives?: number[]; frame_b64?: string; objects?: SceneObject[]; live?: { object_id: string; bbox: BBox }; count: number; count_item?: number }
export interface V2MemoryState { v: V2; type: "memory_state"; on: boolean; memory_id?: string; count: number; crops?: number; bytes?: number; encrypted?: boolean; retention_days?: number; item?: string; count_item?: number }

// Voice-first general product shopping; prices include known shipping, before tax.
export interface ShoppingOffer {
  store: string; price: number; currency: string; delivery_days: number | null;
  pickup_today: boolean; url: string; in_stock: boolean; notes: string; selected: boolean;
}
export interface ShoppingResponse { v: V; product: string; items: ShoppingOffer[]; note?: string }
