/* London Flat Finder — static site served from GitHub Pages. */

// Fill these in once the Supabase project exists; empty = likes UI stays hidden.
const SUPABASE_URL = "https://wielbwysxicciecjbcfl.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_WZe5odmah643LyQPQGIBjw_3Lud3Wpz";
const SITE = "london";

let data = { listings: [] };
let map = null;
let markers = null;
let sb = null;                 // supabase client
let likes = {};                // { listing_id: Set(person) }
let pendingLike = null;        // listing id waiting for identity choice

const $ = (s) => document.querySelector(s);
const PEOPLE = ["Rehan", "Clara"];

// ---- travel time estimates ---------------------------------------------------
// Straight-line distance x 1.35 road circuity, then mode speeds. These are
// estimates for comparing flats, not journey planning.
const DESTS = {
  "Waterloo": [51.5031, -0.1132],
  "St Thomas'": [51.4980, -0.1187],
  "King's Cross": [51.5308, -0.1238],
  "Liverpool St": [51.5178, -0.0817],
};

function kmTo(l, dest) {
  const [dlat, dlng] = DESTS[dest];
  const x = (dlng - l.lng) * Math.cos((l.lat + dlat) / 2 * Math.PI / 180);
  const y = dlat - l.lat;
  return Math.sqrt(x * x + y * y) * 111.32 * 1.35;
}

const MODES = {
  walk: { label: "🚶", mins: (km) => km / 4.8 * 60 },
  bike: { label: "🚲", mins: (km) => 3 + km / 14 * 60 },
  pt:   { label: "🚇", mins: (km) => 10 + km / 22 * 60 },
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
  $("#meta").textContent =
    `${data.listings.length} flats · ≤£${c.max_price} pcm · zones 1–${c.max_zone} · ` +
    `updated ${data.generated}`;
}

// ---- likes (Supabase) --------------------------------------------------------

function whoAmI() { return localStorage.getItem("ff_who"); }

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
  if (mine) {
    likes[id].delete(who);
    if (!likes[id].size) delete likes[id];
    await sb.from("likes").delete().match({ site: SITE, listing_id: id, person: who });
  } else {
    (likes[id] ??= new Set()).add(who);
    await sb.from("likes").insert({ site: SITE, listing_id: id, person: who });
  }
  render(true);
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
  const pmin = +$("#pmin").value || 0;
  const pmax = +$("#pmax").value || Infinity;
  const beds = $("#beds").value;
  const furnished = $("#furnished").value;
  const stationMax = $("#stationMax").value;
  const epcMin = $("#epcMin").value;
  const zones = new Set(
    [...document.querySelectorAll("#zones input:checked")].map((c) => +c.value),
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
    ls = beds === "3"
      ? ls.filter((l) => l.beds >= 3)
      : ls.filter((l) => l.beds === +beds);
  }
  if (furnished !== "any") ls = ls.filter((l) => l.furnished === furnished);
  if (wantBalcony || wantGarden) {
    ls = ls.filter((l) =>
      (wantBalcony && l.outdoor.includes("balcony/terrace")) ||
      (wantGarden && l.outdoor.includes("garden")));
  }
  if (wantLiving) ls = ls.filter((l) => l.receptions >= 1);
  ls = ls.filter((l) => zones.has(l.zone));
  if (stationMax !== "any") ls = ls.filter((l) => l.station_km != null && l.station_km <= +stationMax);
  if (epcMin !== "any") ls = ls.filter((l) => l.epc && EPC_ORDER[l.epc] <= EPC_ORDER[epcMin]);
  if (ttDest && ttMax) ls = ls.filter((l) => travelMins(l, ttDest, ttMode) <= ttMax);

  if (tab === "all") {
    const lf = $("#likedFilter").value;
    if (lf === "liked") ls = ls.filter((l) => likes[l.id]);
    if (lf === "unliked") ls = ls.filter((l) => !likes[l.id]);
  }
  if (tab === "liked") {
    const by = $("#likedBy").value;
    ls = ls.filter((l) => {
      const s = likes[l.id];
      if (!s) return false;
      if (by === "any") return true;
      if (by === "both") return PEOPLE.every((p) => s.has(p));
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
    zone: (a, b) => a.zone - b.zone || a.price_num - b.price_num,
  }[$("#sort").value];
  return ls.sort(cmp);
}

// ---- cards -------------------------------------------------------------------

function travelBlock(l) {
  if (!$("#showTT").checked) return "";
  const rows = Object.keys(DESTS).map((d) =>
    `<span class="ttrow"><b>${d}</b> ` +
    Object.entries(MODES).map(([m, cfg]) => `${cfg.label}${travelMins(l, d, m)}′`).join(" ") +
    `</span>`).join("");
  return `<span class="tt" title="Estimated from distance — not live journey times">${rows}</span>`;
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
      <span class="specs">${l.beds} bed${l.baths ? ` · ${l.baths} bath` : ""}${l.receptions ? ` · ${l.receptions} recep` : ""} · ~zone ${l.zone}${stationLine}</span>
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
    map = L.map("map").setView([51.5074, -0.1278], 11);
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
      `<a href="${l.url}" target="_blank" rel="noopener">View on Zoopla →</a>`,
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
  $("#count").textContent = `${ls.length} flat${ls.length === 1 ? "" : "s"} match`;
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

["from", "to", "pmin", "pmax", "beds", "furnished", "fBalcony", "fGarden",
 "fLiving", "stationMax", "sort", "ttDest", "ttMode", "ttMax", "showTT",
 "availOnly", "epcMin", "likedBy", "likedFilter"].forEach((id) =>
  $("#" + id).addEventListener("change", render),
);
document.querySelectorAll("#zones input").forEach((c) =>
  c.addEventListener("change", render),
);
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
document.querySelectorAll("#whoDialog button").forEach((b) =>
  b.addEventListener("click", () => {
    localStorage.setItem("ff_who", b.dataset.who);
    $("#whoDialog").close();
    if (pendingLike) { const id = pendingLike; pendingLike = null; toggleLike(id); }
  }),
);

(async () => {
  if (SUPABASE_URL && SUPABASE_ANON_KEY && window.supabase) {
    sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }
  if (!sb) document.querySelector('#tabs [data-tab="liked"]').hidden = true;
  if (sb) $("#likedFilter").hidden = false;
  await Promise.all([loadData(), loadLikes()]);
  const [start, end] = data.criteria.window;
  $("#from").value = start;
  $("#to").value = end;
  // these controls only appear once the data actually carries the fields
  if (!data.listings.some((l) => l.furnished)) $("#furnished").hidden = true;
  if (!data.listings.some((l) => l.epc)) $("#epcMin").hidden = true;
  render();
  if (sb) setInterval(async () => { await loadLikes(); render(true); }, 60_000);
})();
