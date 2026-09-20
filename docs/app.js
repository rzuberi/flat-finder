/* Flat finder — static site; everything city-specific comes from data.json. */

const SUPABASE_URL = "https://wielbwysxicciecjbcfl.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_WZe5odmah643LyQPQGIBjw_3Lud3Wpz";

let data = { listings: [], criteria: {} };
let SITE = "";
let PEOPLE = [];
let DESTS = {};
let MODE_KEYS = [];
let map = null;
let markers = null;
let sb = null;                 // supabase client
let likes = {};                // { listing_id: Set(person) }
let pendingLike = null;        // listing id waiting for identity choice

const $ = (s) => document.querySelector(s);

// ---- travel time estimates ---------------------------------------------------
// Straight-line distance x 1.35 road circuity, then mode speeds. Real TfL
// times replace the public-transport estimate where the data has them.
function kmTo(l, dest) {
  const [dlat, dlng] = DESTS[dest];
  const x = (dlng - l.lng) * Math.cos((l.lat + dlat) / 2 * Math.PI / 180);
  const y = dlat - l.lat;
  return Math.sqrt(x * x + y * y) * 111.32 * 1.35;
}

const MODES = {
  walk: { label: "🚶", name: "walking", mins: (km) => km / 4.8 * 60 },
  bike: { label: "🚲", name: "by bike", mins: (km) => 3 + km / 14 * 60 },
  pt:   { label: "🚌", name: "by public transport", mins: (km) => 10 + km / 22 * 60 },
};

function travelMins(l, dest, mode) {
  if (mode === "pt" && l.pt && l.pt[dest] != null) return l.pt[dest];
  return Math.round(MODES[mode].mins(kmTo(l, dest)));
}

// ---- load ------------------------------------------------------------------
async function loadData() {
  const r = await fetch("data.json", { cache: "no-store" });
  data = await r.json();
  const c = data.criteria;
  SITE = c.key;
  PEOPLE = c.people || [];
  DESTS = c.destinations || {};
  MODE_KEYS = c.modes || ["walk", "pt"];
  document.title = c.title;
  $("#title").textContent = `${c.emoji || ""} ${c.title}`.trim();
  $("#favicon").href = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>${c.emoji || "🏠"}</text></svg>`;
  const areaText = c.max_zone ? `zones 1–${c.max_zone}` : `${(c.areas || []).length} areas`;
  $("#meta").textContent =
    `${data.listings.length} homes · ≤£${c.max_price} pcm · ${areaText} · updated ${data.generated}`;
}

function buildControls() {
  const c = data.criteria;
  $("#beds").innerHTML = c.beds_options
    .map(([v, label]) => `<option value="${v}" ${v === c.beds_default ? "selected" : ""}>${label}</option>`).join("");

  const zoneItems = c.areas
    ? c.areas.map((a) => [a, a === c.highlight ? `★ ${a}` : a])
    : Array.from({ length: c.max_zone }, (_, i) => [String(i + 1), String(i + 1)]);
  $("#zones").innerHTML = (c.areas ? "" : "zones ") + zoneItems
    .map(([v, label]) => `<label class="chk"><input type="checkbox" value="${v}" checked> ${label}</label>`).join("");
  $("#zones").title = c.areas ? "Untick areas to hide them" : "Untick zones to hide them";

  $("#ttDest").innerHTML = `<option value="">Travel time to…</option>` +
    Object.keys(DESTS).map((d) => `<option value="${d}">${d}</option>`).join("");
  $("#ttMode").innerHTML = MODE_KEYS
    .map((m) => `<option value="${m}">${MODES[m].name}</option>`).join("");

  $("#likedBy").innerHTML = `<option value="any">Liked by anyone</option>` +
    PEOPLE.map((p) => `<option value="${p}">Liked by ${p}</option>`).join("") +
    `<option value="all">Liked by ${PEOPLE.length > 2 ? "everyone" : "both"}</option>`;
  $("#whoButtons").innerHTML = PEOPLE
    .map((p) => `<button data-who="${p}">${p}</button>`).join("");
}

// ---- likes (Supabase) --------------------------------------------------------

const whoKey = () => `ff_who_${SITE}`;
function whoAmI() { return localStorage.getItem(whoKey()); }

async function loadLikes() {
  if (!sb) return;
  const { data: rows, error } = await sb.from("likes")
    .select("listing_id, person").eq("site", SITE);
  if (error) return;
  likes = {};
  for (const r of rows) (likes[r.listing_id] ??= new Set()).add(r.person);
}

async function toggleLike(id) {
  if (!sb) return;
  if (!whoAmI()) { pendingLike = id; $("#whoDialog").showModal(); return; }
  const who = whoAmI();
  const mine = likes[id]?.has(who);
  let error;
  if (mine) {
    likes[id].delete(who);
    if (!likes[id].size) delete likes[id];
    ({ error } = await sb.from("likes").delete().match({ site: SITE, listing_id: id, person: who }));
  } else {
    (likes[id] ??= new Set()).add(who);
    ({ error } = await sb.from("likes").insert({ site: SITE, listing_id: id, person: who }));
  }
  if (error) { toast(`Couldn't save like: ${error.message}`); await loadLikes(); }
  render(true);
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 3500);
}

// ---- filtering ---------------------------------------------------------------

let tab = "all";

function moveInPass(l) {
  const from = $("#from").value;
  const to = $("#to").value;
  if (l.date_unknown) return true;
  if (!l.available) {
    // available right now: only relevant when the range starts today or earlier
    return !from || from <= new Date().toISOString().slice(0, 10);
  }
  if (from && l.available < from) return false;
  if (to && l.available > to) return false;
  return true;
}

const EPC_ORDER = { A: 1, B: 2, C: 3, D: 4, E: 5, F: 6, G: 7 };

function visibleListings() {
  const c = data.criteria;
  const pmin = +$("#pmin").value || 0;
  const pmax = +$("#pmax").value || Infinity;
  const beds = $("#beds").value;
  const furnished = $("#furnished").value;
  const stationMax = $("#stationMax").value;
  const epcMin = $("#epcMin").value;
  const zones = new Set(
    [...document.querySelectorAll("#zones input:checked")].map((x) => x.value),
  );
  const wantBalcony = $("#fBalcony").checked;
  const wantGarden = $("#fGarden").checked;
  const wantLiving = $("#fLiving").checked;
  const ttDest = $("#ttDest").value;
  const ttMax = +$("#ttMax").value;
  const ttMode = $("#ttMode").value;

  let ls = data.listings.filter(moveInPass);
  if ($("#availOnly").checked) ls = ls.filter((l) => !l.unavailable);
  ls = ls.filter((l) => l.price_num >= pmin && l.price_num <= pmax);
  if (beds !== "any") {
    // the highest listed option means "that many or more"
    const top = c.beds_options.filter(([v]) => v !== "any").map(([v]) => +v).sort((a, b) => b - a)[0];
    ls = +beds === top ? ls.filter((l) => l.beds >= +beds) : ls.filter((l) => l.beds === +beds);
  }
  if (furnished !== "any") ls = ls.filter((l) => l.furnished === furnished);
  if (wantBalcony || wantGarden) {
    ls = ls.filter((l) =>
      (wantBalcony && l.outdoor.includes("balcony/terrace")) ||
      (wantGarden && l.outdoor.includes("garden")));
  }
  if (wantLiving) ls = ls.filter((l) => l.receptions >= 1);
  ls = ls.filter((l) => zones.has(c.areas ? l.area : String(l.zone)));
  if (stationMax !== "any") ls = ls.filter((l) => l.station_km != null && l.station_km <= +stationMax);
  if (epcMin !== "any") ls = ls.filter((l) => l.epc && EPC_ORDER[l.epc] <= EPC_ORDER[epcMin]);
  if (ttDest && ttMax) ls = ls.filter((l) => travelMins(l, ttDest, ttMode) <= ttMax);

  if (tab === "liked") {
    const by = $("#likedBy").value;
    ls = ls.filter((l) => {
      const s = likes[l.id];
      if (!s) return false;
      if (by === "any") return true;
      if (by === "all") return PEOPLE.every((p) => s.has(p));
      return s.has(by);
    });
  }

  const avail = (l) => l.available || "0000-00-00";
  const cmp = {
    num: (a, b) => a.num - b.num,
    priceAsc: (a, b) => a.price_num - b.price_num,
    priceDesc: (a, b) => b.price_num - a.price_num,
    available: (a, b) => avail(a).localeCompare(avail(b)),
    station: (a, b) => (a.station_km ?? 99) - (b.station_km ?? 99),
    zone: (a, b) => (a.centre_km ?? a.zone) - (b.centre_km ?? b.zone) || a.price_num - b.price_num,
  }[$("#sort").value];
  return ls.sort(cmp);
}

// ---- cards -------------------------------------------------------------------

function travelBlock(l) {
  if (!$("#showTT").checked) return "";
  const rows = Object.keys(DESTS).map((d) =>
    `<span class="ttrow"><b>${d}</b> ` +
    MODE_KEYS.map((m) => `${MODES[m].label}${travelMins(l, d, m)}′`).join(" ") +
    `</span>`).join("");
  return `<span class="tt" title="Estimated from distance — not live journey times">${rows}</span>`;
}

function placeLabel(l) {
  return l.area ? l.area : `~zone ${l.zone}`;
}

function card(l) {
  const el = document.createElement("div");
  el.className = "card" + (l.unavailable ? " gone" : "");
  const isNew = l.first_seen === (data.generated || "").slice(0, 10);
  let img = 0;
  const stationLine = l.station
    ? ` · ${l.station_km < 1 ? Math.round(l.station_km * 1000) + " m" : l.station_km + " km"} to ${l.station}`
    : "";
  const liked = likes[l.id];
  const likeBtn = sb
    ? `<button class="heart ${liked?.has(whoAmI()) ? "on" : ""}" title="Like">${liked?.size ? "❤️" : "🤍"}</button>`
    : "";
  el.innerHTML = `
    <div class="photo">
      <span class="num">#${l.num}</span>
      ${l.unavailable ? '<span class="goneflag">NOT AVAILABLE ANYMORE</span>'
        : isNew ? '<span class="newflag">NEW</span>' : ""}
      ${likeBtn}
      <img loading="lazy" src="${l.images[0] || ""}" alt="">
      ${l.images.length > 1 ? `
        <button class="navbtn prev">‹</button>
        <button class="navbtn next">›</button>
        <span class="count">1/${l.images.length}</span>` : ""}
    </div>
    <div class="body">
      <span class="price">${l.price}</span>
      <span class="addr">${l.address}</span>
      <span class="specs">${l.beds === 0 ? "studio" : `${l.beds} bed`}${l.baths ? ` · ${l.baths} bath` : ""}${l.receptions ? ` · ${l.receptions} recep` : ""} · ${placeLabel(l)}${stationLine}</span>
      <span class="badges">
        <span class="badge avail">${l.date_unknown ? "move-in date unknown" : l.available ? `move in ${l.available}` : "available now"}</span>
        ${l.source ? `<span class="badge src">${l.source}</span>` : ""}
        ${l.outdoor.map((o) => `<span class="badge">🌿 ${o}</span>`).join("")}
        ${l.furnished ? `<span class="badge">${l.furnished}</span>` : ""}
        ${l.epc ? `<span class="badge">EPC ${l.epc}</span>` : ""}
      </span>
      ${liked?.size ? `<span class="hearts-by">❤️ ${[...liked].join(" & ")}</span>` : ""}
      ${travelBlock(l)}
      <span class="summary">${l.summary}</span>
      <a class="zlink" href="${l.url}" target="_blank" rel="noopener">View on ${l.source || "Zoopla"} →</a>
      ${Object.entries(l.also_on || {}).map(([s, u]) =>
        `<a class="zlink also" href="${u}" target="_blank" rel="noopener">also on ${s} →</a>`).join("")}
    </div>`;
  el.querySelector(".heart")?.addEventListener("click", () => toggleLike(l.id));
  const imgEl = el.querySelector("img");
  const cnt = el.querySelector(".count");
  const show = (d) => {
    img = (img + d + l.images.length) % l.images.length;
    imgEl.src = l.images[img];
    if (cnt) cnt.textContent = `${img + 1}/${l.images.length}`;
  };
  el.querySelector(".prev")?.addEventListener("click", () => show(-1));
  el.querySelector(".next")?.addEventListener("click", () => show(1));
  return el;
}

// ---- map ---------------------------------------------------------------------

function renderMap(ls) {
  if (!map) {
    const c = data.criteria;
    map = L.map("map").setView(c.centre || [51.5074, -0.1278], c.max_zone ? 11 : 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    markers = L.markerClusterGroup();
    map.addLayer(markers);
  }
  markers.clearLayers();
  for (const l of ls) {
    if (l.lat == null) continue;
    const m = L.marker([l.lat, l.lng]);
    m.bindPopup(
      `<b>#${l.num} · ${l.price}</b><br>${l.address}<br>` +
      `${l.beds} bed · ${l.available ? "move in " + l.available : "available now"}<br>` +
      `<a href="${l.url}" target="_blank" rel="noopener">View on ${l.source || "Zoopla"} →</a>`,
    );
    markers.addLayer(m);
  }
  setTimeout(() => map.invalidateSize(), 50);
}

// ---- render --------------------------------------------------------------------

const PAGE = 300;
let shown = PAGE;
let mapMode = false;

function render(keepShown) {
  if (!keepShown) shown = PAGE;
  const ls = visibleListings();
  $("#count").textContent = `${ls.length} home${ls.length === 1 ? "" : "s"} match`;
  $("#empty").hidden = ls.length > 0;

  $("#map").hidden = !mapMode;
  if (mapMode) renderMap(ls);
  const grid = $("#grid");
  grid.replaceChildren(...ls.slice(0, shown).map(card));
  if (ls.length > shown) {
    const more = document.createElement("button");
    more.className = "more";
    more.textContent = `Show more (${ls.length - shown} left)`;
    more.onclick = () => { shown += PAGE; render(true); };
    grid.append(more);
  }
}

// ---- init ------------------------------------------------------------------

$("#viewToggle").onclick = () => {
  mapMode = !mapMode;
  $("#viewToggle").textContent = mapMode ? "✕ Hide map" : "🗺 Map";
  render(true);
};
document.querySelectorAll("#tabs .tab").forEach((b) =>
  b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll("#tabs .tab").forEach((x) =>
      x.classList.toggle("active", x === b));
    $("#likedBy").hidden = tab !== "liked";
    render();
  }),
);

(async () => {
  if (SUPABASE_URL && SUPABASE_ANON_KEY && window.supabase) {
    sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }
  if (!sb) document.querySelector('#tabs [data-tab="liked"]').hidden = true;
  await loadData();
  buildControls();
  await loadLikes();

  ["from", "to", "pmin", "pmax", "beds", "furnished", "fBalcony", "fGarden",
   "fLiving", "stationMax", "sort", "ttDest", "ttMode", "ttMax", "showTT",
   "availOnly", "epcMin", "likedBy"].forEach((id) =>
    $("#" + id).addEventListener("change", render),
  );
  document.querySelectorAll("#zones input").forEach((x) =>
    x.addEventListener("change", render),
  );
  document.querySelectorAll("#whoButtons button").forEach((b) =>
    b.addEventListener("click", () => {
      localStorage.setItem(whoKey(), b.dataset.who);
      $("#whoDialog").close();
      if (pendingLike) { const id = pendingLike; pendingLike = null; toggleLike(id); }
    }),
  );

  const [start, end] = data.criteria.window;
  $("#from").value = start;
  $("#to").value = end;
  // these controls only appear once the data actually carries the fields
  $("#furnished").hidden = !data.listings.some((l) => l.furnished);
  $("#epcMin").hidden = !data.listings.some((l) => l.epc);
  render();
  if (sb) setInterval(async () => { await loadLikes(); render(true); }, 60_000);
})();
