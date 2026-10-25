import test from "node:test";
import assert from "node:assert/strict";
import { boxStyle, fmtCallTime, hitCaption } from "./rewind.js";

test("call time and caption", () => {
  assert.equal(fmtCallTime(130.4), "2:10");
  assert.equal(fmtCallTime(-3), "0:00");
  assert.equal(hitCaption({ t_s: 130, sure: true }), "2:10 into the call");
  assert.equal(hitCaption({ t_s: 130, sure: false, alternatives: [402] }), "Best guess: 2:10 into the call, or 6:42");
});

test("box style is clamped percent of the frame", () => {
  assert.deepEqual(boxStyle([0.4, 0.3, 0.2, 1.4]), { left: "40%", top: "30%", width: "20%", height: "100%" });
});

function fakeDoc() {
  const make = (tag) => ({
    tag, children: [], className: "", textContent: "",
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren() { this.children = []; },
  });
  return { createElement: make, createTextNode: (text) => ({ tag: "#text", textContent: text }) };
}

test("recap renders ordered steps and the skipped line", async () => {
  const { renderRecap } = await import("./rewind.js");
  const doc = fakeDoc();
  const body = doc.createElement("div");
  renderRecap(doc, body, { steps: [{ t_s: 42, text: "Installed the CPU" }], skipped: ["install the RAM"] });
  const [list, skipped] = body.children;
  assert.equal(list.tag, "ol");
  assert.equal(list.children[0].children[0].textContent, "0:42");
  assert.equal(list.children[0].children[1].textContent, " Installed the CPU");
  assert.equal(skipped.textContent, "Maybe skipped: install the RAM");
  renderRecap(doc, body, { pending: true });
  assert.match(body.children[0].textContent, /Making your recap/);
});

test("recap request carries the page token", async () => {
  const { fetchRecap } = await import("./rewind.js");
  const seen = [];
  globalThis.fetch = async (url, init) => { seen.push([url, init]); return { ok: true, json: async () => (url === "/api/call-owner" ? { owner: "synthetic-owner" } : { steps: [] }) }; };
  await fetchRecap("s1", "?token=abc");
  assert.equal(seen[1][0], "/api/rewind/recap");
  assert.equal(seen[1][1].headers.Authorization, "Bearer abc");
  assert.equal(seen[1][1].headers["X-Aeye-Owner"], "synthetic-owner");
  assert.equal(JSON.parse(seen[1][1].body).session_id, "s1");
});

test("one Rewind state for the button and the switch; notice only on the first enable", async () => {
  const { initialRewind, nextRewind, showsRewindChip } = await import("./rewind.js");
  let s = nextRewind(initialRewind(), { type: "switch", on: true });
  assert.equal(s.on, true);
  assert.equal(s.notice, true);
  s = nextRewind(s, { type: "notice_seen" });
  s = nextRewind(s, { type: "switch", on: false });
  s = nextRewind(s, { type: "switch", on: true });
  assert.equal(s.notice, false);
  assert.equal(showsRewindChip(s), false); // on, but no clips yet
  s = nextRewind(s, { type: "server", on: true, clips: 2 });
  assert.equal(showsRewindChip(s), true);
  s = nextRewind(s, { type: "server", on: true });
  assert.equal(s.clips, 2); // a state without clips keeps the count
  s = nextRewind(s, { type: "switch", on: false });
  assert.equal(showsRewindChip(s), false);
  assert.equal(s.clips, 0);
});

test("prompts are questions the server treats as Rewind", async () => {
  const { PROMPTS } = await import("./rewind.js");
  assert.deepEqual(PROMPTS.map((p) => p.text), ["When did I ", "Where did I put "]);
});
