// Caregiver page: consents, things and their homes, allowed rooms, keeping time and the stop switch.
// Everything is stored through /api/memory/settings (memory.js); nothing goes to localStorage.
import { cleanSettings, createMemoryApi, parseTag, retentionLine, ROOMS, roomAllowed } from "./memory.js";

const api = createMemoryApi();
const $ = (id) => document.getElementById(id);
let settings = cleanSettings();
const token = new URLSearchParams(location.search).get("token");
let bytes = 0;

function render() {
  $("consent-person").checked = settings.consent.person;
  $("consent-caregiver").checked = settings.consent.caregiver;
  const both = settings.consent.person && settings.consent.caregiver;
  $("remembering").disabled = !both;
  $("remembering").checked = settings.remembering;
  $("remember-foot").textContent = both
    ? "Turn this off to stop remembering at once. Nothing new is kept while it is off."
    : "Both of you need to agree before A-eye can remember anything.";
  const list = $("items");
  list.replaceChildren();
  for (const [i, item] of settings.items.entries()) {
    const li = document.createElement("li");
    li.className = "row item-row";
    const text = document.createElement("span");
    const what = document.createElement("b");
    what.textContent = item.item;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "→";
    arrow.setAttribute("aria-label", "lives in");
    text.append(what, arrow, document.createTextNode(item.home));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-btn";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${item.item}`);
    remove.addEventListener("click", () => {
      settings.items.splice(i, 1);
      render();
    });
    li.append(text, remove);
    list.append(li);
  }
  const rooms = $("rooms");
  rooms.replaceChildren();
  for (const room of ROOMS) {
    const label = document.createElement("label");
    label.className = "row";
    const name = document.createElement("span");
    name.textContent = room[0].toUpperCase() + room.slice(1);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.setAttribute("aria-label", room);
    if (!roomAllowed(room)) {
      label.classList.add("refused");
      box.disabled = true;
      box.checked = false;
      const why = document.createElement("small");
      why.textContent = "Private room, never remembered";
      name.append(why);
    } else {
      box.checked = settings.rooms.includes(room);
      box.addEventListener("change", () => {
        settings.rooms = box.checked ? [...settings.rooms, room] : settings.rooms.filter((r) => r !== room);
      });
    }
    label.append(name, box);
    rooms.append(label);
  }
  $("kept-days").value = String(settings.kept_days);
  $("retention").textContent = retentionLine(settings.kept_days, bytes);
}

async function save(note) {
  if (token) return;
  $("save").disabled = true;
  try {
    const result = await api.save(settings);
    settings = cleanSettings(result);
    $("preview").classList.toggle("hidden", !result.preview);
    $("saved").textContent = result.preview ? `${note} (preview only, not saved)` : note;
  } catch {
    $("saved").textContent = "Could not save. Check the connection and try again.";
  } finally {
    $("save").disabled = false;
    render();
  }
}

$("consent-person").addEventListener("change", (e) => {
  settings.consent.person = e.target.checked;
  settings = cleanSettings(settings);
  render();
});
$("consent-caregiver").addEventListener("change", (e) => {
  settings.consent.caregiver = e.target.checked;
  settings = cleanSettings(settings);
  render();
});
// The stop switch saves at once: stopping must not wait for the Save button.
$("remembering").addEventListener("change", (e) => {
  settings.remembering = e.target.checked;
  void save(e.target.checked ? "Remembering is on." : "Stopped remembering.");
});
$("add-item").addEventListener("submit", (e) => {
  e.preventDefault();
  const tag = parseTag($("item-text").value);
  if (!tag) {
    $("item-foot").textContent = "Write it like: keys -> blue bowl";
    return;
  }
  if (!roomAllowed(tag.home)) {
    $("item-foot").textContent = "Bedrooms and bathrooms are never remembered.";
    return;
  }
  settings.items = [...settings.items.filter((i) => i.item.toLowerCase() !== tag.item.toLowerCase()), tag];
  $("item-text").value = "";
  $("item-foot").textContent = "Write the thing, an arrow, and where it usually lives.";
  render();
});
$("kept-days").addEventListener("change", (e) => {
  settings.kept_days = Number(e.target.value);
  render();
});
$("save").addEventListener("click", () => void save("Saved."));

(async () => {
  if (token) {
    render();
    document.querySelectorAll('input, button, select').forEach(control => { control.disabled = true; });
    $("saved").textContent = 'Caregiver settings require personal sign-in. Shared-token calls keep separate, temporary memories.';
    return;
  }
  try {
    const loaded = await api.settings();
    settings = cleanSettings(loaded);
    $("preview").classList.toggle("hidden", !loaded.preview);
  } catch {
    $("saved").textContent = "Could not load the settings.";
  }
  try {
    const status = await api.status("");
    bytes = Number(status.bytes) || 0;
  } catch {
    bytes = 0;
  }
  render();
})();
