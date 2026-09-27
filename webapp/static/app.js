const $ = (id) => document.getElementById(id);

const slots = [$("slot1"), $("slot2"), $("slot3"), $("slot4"), $("slot5"), $("slot6")];
const slotArt = [$("slot1art"), $("slot2art"), $("slot3art"), $("slot4art"), $("slot5art"), $("slot6art")];
const statusEl = $("status");
const errorEl = $("error");
const go = $("go");
let roster = [];
let shotFiles = [];

function showError(msg) {
  errorEl.hidden = !msg;
  errorEl.textContent = msg || "";
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function spriteSlug(name) {
  let s = String(name || "").trim().toLowerCase();
  s = s.replace(/^mega\s+/, "");
  s = s.replace(/\s+mega\s*([xyz])?$/, (_, xyz) => "-mega" + (xyz || ""));
  s = s.replace(/['.]/g, "");
  s = s.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  s = s.replace(/-mega-([xyz])$/, "-mega$1");
  s = s.replace(/-rapid-strike$/, "-rapidstrike");
  s = s.replace(/-dawn-wings$/, "-dawnwings");
  s = s.replace(/-dusk-mane$/, "-duskmane");
  s = s.replace(/-paldea-([a-z]+)$/, "-paldea$1");
  return s;
}

function itemSlug(name) {
  return String(name || "")
    .trim()
    .toLowerCase()
    .replace(/['.]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function pokeSrcs(name) {
  const slug = spriteSlug(name);
  return [
    `https://play.pokemonshowdown.com/sprites/dex/${slug}.png`,
    `https://play.pokemonshowdown.com/sprites/gen5/${slug}.png`,
    `https://play.pokemonshowdown.com/sprites/home/${slug}.png`,
  ];
}

function itemSrcs(name) {
  const slug = itemSlug(name);
  if (!slug) return [];
  return [
    `https://play.pokemonshowdown.com/sprites/itemicons/${slug}.png`,
    `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/${slug}.png`,
    `https://img.pokemondb.net/sprites/items/${slug}.png`,
  ];
}

function fallbackImg(img) {
  const rest = (img.dataset.alts || "").split(",").filter(Boolean);
  if (!rest.length) {
    img.hidden = true;
    img.removeAttribute("src");
    return;
  }
  img.dataset.alts = rest.slice(1).join(",");
  img.src = rest[0];
}

window.fallbackImg = fallbackImg;

function imgTag(name, kind, cls) {
  const srcs = kind === "item" ? itemSrcs(name) : pokeSrcs(name);
  if (!srcs.length) return "";
  return `<img class="${cls}" alt="${esc(name)}" src="${srcs[0]}" data-alts="${esc(srcs.slice(1).join(","))}" onerror="fallbackImg(this)">`;
}

function previewSeed(input, art) {
  const name = input.value.trim();
  if (!name) {
    art.hidden = true;
    art.removeAttribute("src");
    return;
  }
  const srcs = pokeSrcs(name);
  art.hidden = false;
  art.dataset.alts = srcs.slice(1).join(",");
  art.alt = name;
  art.src = srcs[0];
}

function ensureMoveFields() {
  if (!document.getElementById("move-field-css")) {
    const css = document.createElement("style");
    css.id = "move-field-css";
    css.textContent =
      ".move-row{display:flex;align-items:center;gap:8px;margin-top:8px}" +
      ".move-label{flex:0 0 40px;font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.04em}" +
      "form input.seed-move{margin-top:0;height:36px;font-size:14px;width:100%;padding:0 10px;border:1px solid #ccc;border-radius:6px}";
    document.head.appendChild(css);
  }
  [1, 2].forEach((n) => {
    if ($("slot" + n + "move")) return;
    const seed = document.querySelector(`label.seed input#slot${n}`);
    const host = seed && seed.closest("label.seed");
    if (!host) return;
    if (!$("moves" + n)) {
      const list = document.createElement("datalist");
      list.id = "moves" + n;
      document.querySelector("form")?.appendChild(list);
    }
    const row = document.createElement("span");
    row.className = "move-row";
    row.innerHTML =
      `<span class="move-label">Move</span>` +
      `<input id="slot${n}move" class="seed-move" name="slot${n}move" list="moves${n}" placeholder="e.g. ${n === 1 ? "Fake Out" : "Parting Shot"}">`;
    host.appendChild(row);
  });
}

ensureMoveFields();

slots.forEach((el, i) => {
  el.addEventListener("input", () => previewSeed(el, slotArt[i]));
  el.addEventListener("change", () => {
    previewSeed(el, slotArt[i]);
    if (i < 2) loadMoveHints(i);
  });
});

function maxMegasFromForm() {
  const picked = document.querySelector('input[name="maxMegas"]:checked');
  return Number((picked && picked.value) || 2);
}

async function waitForData() {
  for (;;) {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (data.ready) {
        statusEl.textContent = `${data.format_label || "Regulation M-C"} · ${data.teams} unique 6-mon teams · ${data.pokemon} Pokémon in corpus${data.updated ? " · meta " + data.updated : ""}`;
        go.disabled = false;
        const poke = await (await fetch("/api/pokemon")).json();
        $("dex").innerHTML = (poke.names || [])
          .map((n) => `<option value="${esc(n)}"></option>`)
          .join("");
        return;
      }
      if (data.error) {
        statusEl.textContent = "Could not load battle data.";
        showError(data.error);
        return;
      }
      statusEl.textContent = "Loading battle data…";
    } catch (err) {
      statusEl.textContent = "Waiting for server…";
    }
    await new Promise((r) => setTimeout(r, 800));
  }
}

function seedsFromForm() {
  return slots.map((el) => el.value.trim()).filter(Boolean);
}

function seedMovesFromForm() {
  return [0, 1].map((i) => ({
    pokemon: slots[i].value.trim(),
    move: (($(i === 0 ? "slot1move" : "slot2move") || {}).value || "").trim(),
  }));
}

async function loadMoveHints(slotIndex) {
  const input = slots[slotIndex];
  const list = $(slotIndex === 0 ? "moves1" : "moves2");
  if (!input || !list) return;
  const name = input.value.trim();
  if (!name) {
    list.innerHTML = "";
    return;
  }
  try {
    const res = await fetch(`/api/moves?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    list.innerHTML = (data.moves || [])
      .map((m) => `<option value="${esc(m.name)}"></option>`)
      .join("");
  } catch (_) {
    list.innerHTML = "";
  }
}

function addPartner(name) {
  const empty = slots.find((el) => !el.value.trim());
  if (!empty) {
    showError("All six slots are full. Clear one first.");
    return;
  }
  empty.value = name;
  previewSeed(empty, slotArt[slots.indexOf(empty)]);
}

$("builder").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  showError("");
  const seeds = seedsFromForm();
  if (!seeds.length) {
    showError("Pick at least one Pokémon.");
    return;
  }
  go.disabled = true;
  go.textContent = "Working…";
  try {
    const res = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seeds,
        seed_moves: seedMovesFromForm(),
        max_megas: maxMegasFromForm(),
        roster,
        auto_roster: seeds.length === 0,
        top_n: 8,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error || "Recommendation failed.");
      hideResults();
      return;
    }
    render(data);
  } catch (err) {
    showError(String(err));
  } finally {
    go.disabled = false;
    go.textContent = "Recommend";
  }
});

function hideResults() {
  ["resolved", "partners", "why", "sets", "threats", "team"].forEach((id) => {
    const el = $(id);
    if (el) el.hidden = true;
  });
}

function render(data) {
  $("resolvedList").innerHTML = data.resolved
    .map((r) => {
      const item = r.item
        ? `<span class="item-line">${imgTag(r.item, "item", "item-inline")} ${esc(r.item)}</span>`
        : "";
      return `<div class="chip">
        <div class="art-wrap">${imgTag(r.canonical || r.query, "poke", "sprite sm")}</div>
        <div class="body">
          <div class="name">${esc(r.label)}</div>
          <div class="meta">${esc(r.kind)} · support ${Math.round(r.support)}</div>
          ${item}
        </div>
      </div>`;
    })
    .join("");
  $("resolved").hidden = false;

  $("partnerList").innerHTML = (data.partners || [])
    .map((p) => `<button type="button" class="card partner" data-add="${esc(p.pokemon)}">
      <div class="art-wrap">${imgTag(p.pokemon, "poke", "sprite")}</div>
      <div class="body">
        <div class="name">${esc(p.pokemon)} <span class="pill ${esc(p.confidence || "")}">${esc(p.confidence || "")}</span></div>
        <div class="meta">lift ${esc(p.avg_lift)} · together ${esc(p.avg_together_pct)}</div>
        <div class="meta">anchors ${esc(p.anchor_coverage_pct)} · WR ${esc(p.pair_winrate_pct || "—")}</div>
      </div>
    </button>`).join("") || `<p class="meta">No partners with enough sample size.</p>`;
  $("partners").hidden = false;
  $("partnerList").querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => addPartner(btn.dataset.add));
  });

  const why = data.why || {};
  $("whySummary").textContent = why.summary || "";
  $("whyList").innerHTML = (why.points || [])
    .map((pt) => {
      const art = pt.pokemon
        ? `<div class="art-wrap">${imgTag(pt.pokemon, "poke", "sprite sm")}</div>`
        : "";
      return `<li class="chip">
        ${art}
        <div class="body">
          <div class="name">${esc(pt.title || "")}</div>
          <div class="meta">${esc(pt.text || "")}</div>
        </div>
      </li>`;
    })
    .join("");
  $("why").hidden = !(why.summary || (why.points && why.points.length));

  $("setList").innerHTML = (data.sets || [])
    .map((s) => {
      const items = (s.items || [])
        .map((i) => `<span class="item-line">${imgTag(i.name, "item", "item-inline")} ${esc(i.name)}</span>`)
        .join("") || "—";
      const moves = (s.moves || []).map((m) => esc(m.name)).join(", ") || "—";
      const tag = s.fallback ? `fallback=${s.fallback}` : `slice n=${Math.round(s.n_teams)}`;
      return `<div class="card">
        <div class="chip">
          <div class="art-wrap">${imgTag(s.species, "poke", "sprite")}</div>
          <div class="body">
            <div class="name">${esc(s.species)} <span class="pill ${esc(s.confidence)}">${esc(s.confidence)}</span></div>
            <div class="meta">${esc(tag)}${s.winrate_pct ? " · WR " + esc(s.winrate_pct) : ""}</div>
          </div>
        </div>
        <div class="meta" style="margin-top:8px">${items}</div>
        <div class="meta">Moves: ${moves}</div>
      </div>`;
    })
    .join("");
  $("sets").hidden = false;

  const team = data.team || {};
  $("slotCards").innerHTML = (team.slots || [])
    .map((slot) => {
      const colors = slot.type_colors || [];
      let bg = "";
      if (colors.length >= 2) {
        bg = `background: linear-gradient(135deg, ${colors[0]} 0%, ${colors[0]} 42%, ${colors[1]} 58%, ${colors[1]} 100%);`;
      } else if (colors.length === 1) {
        bg = `background: ${colors[0]};`;
      }
      const typePills = (slot.types || [])
        .map((t, i) => {
          const c = colors[i] || "#ddd";
          return `<span class="type-pill" style="background:${c}">${esc(t)}</span>`;
        })
        .join("");
      return `<div class="card typed" style="${bg}">
      <div class="chip">
        <div class="art-wrap">
          ${imgTag(slot.species, "poke", "sprite")}
          ${slot.item ? imgTag(slot.item, "item", "item-icon") : ""}
        </div>
        <div class="body">
          <div class="name">${esc(slot.species)} <span class="pill ${esc(slot.confidence)}">${esc(slot.confidence)}</span></div>
          <div class="type-row">${typePills}</div>
          <div class="meta">${esc(slot.item || "No item")}</div>
          <div class="meta">${[slot.ability, slot.nature && "Align " + slot.nature, slot.stat_points && "SP " + slot.stat_points].filter(Boolean).map(esc).join(" · ")}</div>
        </div>
      </div>
      <ul class="moves">${(slot.moves || []).map((m) => `<li>- ${esc(m)}</li>`).join("")}</ul>
      ${slot.notes && slot.notes.length ? `<div class="notes">${esc(slot.notes.join("; "))}</div>` : ""}
    </div>`;
    })
    .join("");
  $("paste").textContent = team.paste || "";
  $("team").hidden = false;

  let threatBox = $("threats");
  if (!threatBox) {
    threatBox = document.createElement("section");
    threatBox.id = "threats";
    threatBox.innerHTML = `<h2 class="subheader">Threats to respect</h2>
      <p class="hint">Common M-C Pokémon that usage data marks as poor matchups for this Bring-6. Not a damage calc.</p>
      <div id="threatList" class="grid chips"></div>`;
    const teamBox = $("team");
    teamBox.parentNode.insertBefore(threatBox, teamBox);
  }
  const threatList = $("threatList");
  const threats = data.threats || [];
  if (threatList) {
    threatList.innerHTML = threats.length
      ? threats
          .map((t) => `<div class="chip">
        <div class="art-wrap">${imgTag(t.pokemon, "poke", "sprite sm")}</div>
        <div class="body">
          <div class="name">${esc(t.pokemon)}</div>
          <div class="meta">${esc(t.reason)}</div>
        </div>
      </div>`)
          .join("")
      : `<p class="hint">No threat slice yet — restart the server so /api/recommend includes threats.</p>`;
  }
  threatBox.hidden = false;
}

$("copy").addEventListener("click", async () => {
  const text = $("paste").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    $("copy").textContent = "Copied";
    setTimeout(() => { $("copy").textContent = "Copy paste"; }, 1200);
  } catch {
    showError("Could not copy — select the paste box instead.");
  }
});

function renderIconLabels(icons) {
  let box = $("iconLabels");
  if (!box) return;
  if (!icons.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = icons
    .map((icon, i) => `<label class="icon-label">
      <img src="${icon.image}" alt="" />
      <input list="dex" data-icon="${i}" placeholder="name" />
    </label>`)
    .join("");
}

function addLabeledIcons() {
  const box = $("iconLabels");
  if (!box) return;
  const seen = new Set(roster.map((n) => n.toLowerCase()));
  box.querySelectorAll("input").forEach((input) => {
    const name = input.value.trim();
    if (!name || seen.has(name.toLowerCase())) return;
    seen.add(name.toLowerCase());
    roster.push(name);
  });
  renderRoster();
}

function renderRoster() {
  const box = $("rosterList");
  if (!box) return;
  box.innerHTML = roster
    .map((name) => `<button type="button" class="chip" data-drop="${esc(name)}">
      <div class="art-wrap">${imgTag(name, "poke", "sprite sm")}</div>
      <div class="body"><div class="name">${esc(name)}</div><div class="meta">owned · click to remove</div></div>
    </button>`)
    .join("");
  box.querySelectorAll("[data-drop]").forEach((btn) => {
    btn.addEventListener("click", () => {
      roster = roster.filter((n) => n !== btn.dataset.drop);
      renderRoster();
    });
  });
}

async function fileToData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function scanRoster() {
  showError("");
  const btn = $("scan");
  if (btn) btn.disabled = true;
  try {
    const images = [];
    for (const file of shotFiles) images.push(await fileToData(file));
    const res = await fetch("/api/roster", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ images, text: ($("rosterText") && $("rosterText").value) || "" }),
    });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error || "Scan failed.");
      return;
    }
    const seen = new Set(roster.map((n) => n.toLowerCase()));
    for (const name of data.roster || []) {
      if (!seen.has(name.toLowerCase())) {
        seen.add(name.toLowerCase());
        roster.push(name);
      }
    }
    renderRoster();
    renderIconLabels(data.icons || []);
    if (data.ocr) showError(data.ocr);
    else if (!roster.length && !(data.icons || []).length) {
      showError("No Pokémon names found. Crop to the box, or paste names.");
    }
  } catch (err) {
    showError(String(err));
  } finally {
    if (btn) btn.disabled = false;
  }
}

const drop = $("drop");
const shots = $("shots");
if (drop && shots) {
  drop.addEventListener("click", () => shots.click());
  shots.addEventListener("change", () => {
    shotFiles = Array.from(shots.files || []);
    drop.textContent = shotFiles.length
      ? shotFiles.map((f) => f.name).join(", ")
      : "Drop screenshots here, or click to choose";
  });
  drop.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    drop.classList.remove("over");
    shotFiles = Array.from(ev.dataTransfer.files || []).filter((f) => f.type.startsWith("image/"));
    drop.textContent = shotFiles.length
      ? shotFiles.map((f) => f.name).join(", ")
      : "Drop screenshots here, or click to choose";
  });
}
if ($("scan")) $("scan").addEventListener("click", scanRoster);
if ($("addLabeled")) $("addLabeled").addEventListener("click", addLabeledIcons);

async function buildFromRoster() {
  const inputs = Array.from(document.querySelectorAll("#iconLabels input"));
  if (inputs.length && inputs.some((input) => !input.value.trim())) {
    showError("Fill every sprite name, then build.");
    return;
  }
  addLabeledIcons();
  const typed = (($("rosterText") && $("rosterText").value) || "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const seen = new Set(roster.map((n) => n.toLowerCase()));
  for (const name of typed) {
    if (!seen.has(name.toLowerCase())) {
      seen.add(name.toLowerCase());
      roster.push(name);
    }
  }
  renderRoster();
  if (roster.length < 2) {
    showError("Need at least two owned Pokémon.");
    return;
  }
  showError("");
  const btn = $("buildRoster");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seeds: [],
        auto_roster: true,
        roster,
        max_megas: maxMegasFromForm(),
        top_n: 8,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      showError(data.error || "Could not build from roster.");
      return;
    }
    render(data);
  } catch (err) {
    showError(String(err));
  } finally {
    if (btn) btn.disabled = false;
  }
}
if ($("buildRoster")) $("buildRoster").addEventListener("click", buildFromRoster);

waitForData();
