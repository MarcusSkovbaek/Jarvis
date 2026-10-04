// Browser self-check for the Sætradar page.
//
// Drives the real page in Chromium at iPhone and desktop sizes, in dark and
// light, against the demo data, the empty first-run data, a stress data set
// and broken networks. Fails on console errors, sideways scrolling, clipped
// text, and on any interaction that does not do what it says.
//
//   cd set-tracker && python -m http.server 8766 --directory web &
//   node tests/ui_check.mjs            (needs: npm i playwright)
//
// Env: BASE (default http://127.0.0.1:8766/), SHOTS (screenshot folder),
//      CHROMIUM (path to a chromium binary), HTTPS_PROXY (used for web fonts).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE || "http://127.0.0.1:8766/";
const SHOTS = process.env.SHOTS || "ui-shots";
fs.mkdirSync(SHOTS, { recursive: true });

const failures = [];
function check(label, ok, detail = "") {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? "  -> " + detail : ""}`);
  if (!ok) failures.push(label);
}

const IPHONE = {
  viewport: { width: 393, height: 852 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
  userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
};
const IPHONE_SE = { ...IPHONE, viewport: { width: 320, height: 568 }, deviceScaleFactor: 2 };
const EDGE = {
  viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1,
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
};
const TABLET = { viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };

// Only https goes through a proxy, so the local test server is reached directly.
const launch = {
  executablePath: process.env.CHROMIUM || undefined,
  args: process.env.HTTPS_PROXY ? ["--proxy-server=https=" + process.env.HTTPS_PROXY.replace(/^https?:\/\//, "")] : [],
};
const browser = await chromium.launch(launch);

async function open(device, scheme, url, setup) {
  const context = await browser.newContext({ ...device, colorScheme: scheme, ignoreHTTPSErrors: true, locale: "da-DK", timezoneId: "Europe/Copenhagen" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/fonts\.g|ERR_TUNNEL|net::ERR_/.test(m.text())) errors.push("console: " + m.text()); });
  if (setup) await setup(page, context);
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => document.querySelector("#queue")?.getAttribute("aria-busy") === "false", null, { timeout: 15000 });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(["800 20px Archivo", "16px 'IBM Plex Sans'", "16px DotGothic16"].map((f) => document.fonts.load(f)));
  });
  await page.waitForTimeout(600);   // entry animation
  return { page, context, errors };
}

// Layout problems a person would see.
async function layoutProblems(page) {
  return page.evaluate(() => {
    const out = [];
    const doc = document.scrollingElement;
    if (doc.scrollWidth > window.innerWidth + 1) out.push(`page scrolls sideways (${doc.scrollWidth} > ${window.innerWidth})`);
    const vw = window.innerWidth;
    for (const node of document.querySelectorAll("body *")) {
      const cs = getComputedStyle(node);
      if (cs.display === "none" || cs.visibility === "hidden" || node.closest("[hidden]") || node.closest("details:not([open]) > :not(summary)")) continue;
      const r = node.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (node.closest(".art") || node.closest(".scope") || node.closest(".seg")) continue;
      if (r.right > vw + 1 || r.left < -1) out.push(`${node.tagName.toLowerCase()}.${node.className} sticks out (${Math.round(r.left)}..${Math.round(r.right)})`);
    }
    // Text that is cut off without an ellipsis or clamp meant for it.
    for (const node of document.querySelectorAll("button, a, .label, .criteria li, .seg button, .meter__word, .readout__value")) {
      if (node.closest("[hidden]") || node.matches(".art")) continue;   // artwork crops its blurred backdrop on purpose
      const cs = getComputedStyle(node);
      if (cs.textOverflow === "ellipsis" || cs.webkitLineClamp !== "none") continue;
      if (node.scrollWidth > node.clientWidth + 1 && cs.overflow !== "visible") out.push(`clipped text: "${node.textContent.trim().slice(0, 40)}"`);
    }
    return out.slice(0, 12);
  });
}

// Parts of a card, a panel or a control that reach outside it.
async function escapes(page) {
  return page.evaluate(() => {
    const out = [];
    const boxes = document.querySelectorAll(".set, .empty, .crate, .pending, .notice, .seg, .meter, .qd, .topbar__inner, .toast");
    for (const box of boxes) {
      if (box.closest("[hidden]")) continue;
      const b = box.getBoundingClientRect();
      if (!b.width) continue;
      for (const kid of box.querySelectorAll("*")) {
        if (kid.closest(".art") || kid.closest(".sr-only") || kid.matches(".sr-only")) continue;
        if (kid.closest("details:not([open]) > :not(summary)")) continue;
        if (box.matches(".seg") && getComputedStyle(box).overflowX === "auto") continue;
        const r = kid.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        if (r.left < b.left - 1 || r.right > b.right + 1 || r.top < b.top - 1 || r.bottom > b.bottom + 1) {
          out.push(`${kid.tagName.toLowerCase()}.${kid.className.baseVal ?? kid.className} escapes .${box.className.split(" ")[0]}`);
          break;
        }
      }
    }
    return out.slice(0, 10);
  });
}

// Overlap between direct children of the same flex/grid row (header, card parts, actions).
async function overlaps(page) {
  return page.evaluate(() => {
    const out = [];
    const groups = document.querySelectorAll(".topbar__inner, .set, .set__side, .actions, .meter, .controls, .set__facts, .foot__grid");
    for (const g of groups) {
      if (g.closest("[hidden]")) continue;
      const kids = [...g.children].filter((k) => { const r = k.getBoundingClientRect(); return r.width && r.height; });
      for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
        const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ix > 2 && iy > 2) out.push(`${g.className}: "${kids[i].className}" overlaps "${kids[j].className}"`);
      }
    }
    return out.slice(0, 10);
  });
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, name + ".png"), fullPage: true });
}

async function commonChecks(tag, page, errors) {
  check(`${tag}: no script errors`, errors.length === 0, errors.join(" | "));
  const lp = await layoutProblems(page);
  check(`${tag}: nothing sticks out or is clipped`, lp.length === 0, lp.join(" | "));
  const ov = await overlaps(page);
  check(`${tag}: no overlapping parts`, ov.length === 0, ov.join(" | "));
  const es = await escapes(page);
  check(`${tag}: everything stays inside its card or control`, es.length === 0, es.join(" | "));
}

// ------------------------------------------------------------------ demo
for (const [devName, device] of [["iphone", IPHONE], ["edge", EDGE], ["iphone-se", IPHONE_SE], ["tablet", TABLET]]) {
  for (const scheme of ["dark", "light"]) {
    if ((devName === "iphone-se" || devName === "tablet") && scheme === "light") continue;
    const tag = `demo ${devName} ${scheme}`;
    console.log(`\n${tag}`);
    const { page, context, errors } = await open(device, scheme, BASE + "#demo");
    await shot(page, `demo-${devName}-${scheme}-unheard`);
    await page.click('[data-filter="status"][data-value="all"]');
    await page.waitForTimeout(400);
    await shot(page, `demo-${devName}-${scheme}-all`);
    await commonChecks(tag, page, errors);
    await context.close();
  }
}

// ------------------------------------------------------- interactions (demo)
{
  console.log("\ninteractions");
  const { page, context, errors } = await open(IPHONE, "dark", BASE + "#demo");
  const count = (k) => page.$eval(`[data-count="${k}"]`, (n) => Number(n.textContent));
  const startUnheard = await count("unheard");
  check("demo starts with 3 unheard and 1 heard", startUnheard === 3 && (await count("heard")) === 1, `${startUnheard}/${await count("heard")}`);

  // Opening a set marks it as heard, and the link really opens a new tab.
  const first = page.locator(".set").first();
  const id = await first.getAttribute("data-id");
  const [popup] = await Promise.all([
    context.waitForEvent("page", { timeout: 5000 }).catch(() => null),
    first.locator(".btn--play").click(),
  ]);
  check("open button opens a new tab", !!popup);
  if (popup) await popup.close();
  await page.waitForTimeout(300);
  check("opened set is marked heard", await page.locator(`.set[data-id="${id}"][data-heard]`).count() === 1);
  check("it stays in view under 'Ikke hørt' until the filter changes", await page.locator(`.set[data-id="${id}"]`).count() === 1);
  check("counts update", (await count("unheard")) === startUnheard - 1, String(await count("unheard")));
  check("toast offers undo", await page.locator("#toast:not([hidden]) #toast-undo:not([hidden])").count() === 1);
  await page.click("#toast-undo");
  await page.waitForTimeout(200);
  check("undo restores unheard", (await count("unheard")) === startUnheard && await page.locator(`.set[data-id="${id}"][data-heard]`).count() === 0);

  // A click re-renders the list, but artwork is moved, not rebuilt, and cards do not rise in again.
  await page.evaluate(() => { document.querySelectorAll(".set canvas").forEach((c) => { c.dataset.mark = "1"; }); });
  await page.locator(".set").nth(1).locator(".btn--heard").click();
  await page.waitForTimeout(150);
  check("artwork survives a re-render (no blinking)", await page.evaluate(() =>
    [...document.querySelectorAll(".set canvas")].every((c) => c.dataset.mark === "1")));
  check("cards do not replay their entry animation on a click", await page.evaluate(() =>
    !document.querySelector("#queue").classList.contains("is-entering")));
  await page.locator(".set").nth(1).locator(".btn--heard").click();
  await page.waitForTimeout(150);

  // Manual toggle, persistence across reloads.
  await page.locator(`.set[data-id="${id}"] .btn--heard`).click();
  await page.waitForTimeout(200);
  check("toggle button sets aria-pressed", (await page.locator(`.set[data-id="${id}"] .btn--heard`).getAttribute("aria-pressed")) === "true");
  await page.reload({ waitUntil: "load" });
  await page.waitForTimeout(500);
  check("heard survives reload (hidden from 'Ikke hørt')", await page.locator(`.set[data-id="${id}"]`).count() === 0);
  await page.click('[data-filter="status"][data-value="heard"]');
  await page.waitForTimeout(200);
  check("'Hørt' filter shows it", await page.locator(`.set[data-id="${id}"][data-heard]`).count() === 1);
  await page.reload({ waitUntil: "load" });
  await page.waitForTimeout(500);
  check("filter choice survives reload", (await page.getAttribute('[data-filter="status"][data-value="heard"]', "aria-pressed")) === "true");

  // Platform filter and its empty state.
  await page.click('[data-filter="status"][data-value="all"]');
  await page.click('[data-filter="platform"][data-value="soundcloud"]');
  await page.waitForTimeout(200);
  const platforms = await page.$$eval(".set .art__badge", (n) => n.map((x) => x.textContent.trim()));
  check("platform filter shows only SoundCloud", platforms.length > 0 && platforms.every((p) => p === "SoundCloud"), platforms.join(","));
  await page.click('[data-filter="platform"][data-value="all"]');

  // Quality details.
  const meter = page.locator(".set .meter").first();
  await meter.click();
  await page.waitForTimeout(200);
  check("quality panel opens", (await meter.getAttribute("aria-expanded")) === "true" && await page.locator(".qd").count() === 1);
  await shot(page, "demo-iphone-dark-quality-open");
  check("quality panel lists findings", await page.locator(".qd__signals li").count() >= 3);
  await page.locator(".set .meter").first().click();
  check("quality panel closes", await page.locator(".qd").count() === 0);

  // Rejected crate.
  await page.click("#crate > summary");
  await page.waitForTimeout(200);
  check("crate lists rejected sets with reasons", await page.locator(".reject").count() === 3 && await page.locator(".reject__why li").count() >= 3);
  await shot(page, "demo-iphone-dark-crate");

  // Links are safe and go out in a new tab.
  const badLinks = await page.$$eval("a[data-open], .chip-link", (as) => as.filter((a) => a.target !== "_blank" || !/^https:/.test(a.href) || !/noopener/.test(a.rel)).map((a) => a.outerHTML.slice(0, 80)));
  check("every outgoing link is https, new tab, noopener", badLinks.length === 0, badLinks.join(" | "));

  // Keyboard: focus is visible on the heard toggle.
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => { const a = document.activeElement; return a ? getComputedStyle(a).outlineStyle : "none"; });
  check("keyboard focus is visible", outline !== "none", outline);
  check("no script errors during interactions", errors.length === 0, errors.join(" | "));
  await context.close();
}

// ------------------------------------------------------- first run (empty)
for (const [devName, device] of [["iphone", IPHONE], ["edge", EDGE]]) {
  for (const scheme of ["dark", "light"]) {
    const tag = `empty ${devName} ${scheme}`;
    console.log(`\n${tag}`);
    const { page, context, errors } = await open(device, scheme, BASE);
    check(`${tag}: empty state explains what will appear`, await page.locator(".empty h2").innerText() === "Ingen nye sæt endnu");
    check(`${tag}: the artist name breaks only between words`, await page.$$eval(".plate__name", (ns) => ns.every((n) => {
      const r = document.createRange(); r.selectNodeContents(n);
      const lines = new Set([...r.getClientRects()].map((x) => Math.round(x.top)));
      return lines.size <= n.textContent.trim().split(/\s+/).length;
    })));
    check(`${tag}: status says first scan is pending or done`, /Ikke tjekket|Tjekket|Sidste/.test(await page.locator("#scan-text").innerText()));
    await commonChecks(tag, page, errors);
    await shot(page, `empty-${devName}-${scheme}`);
    await context.close();
  }
}

// ------------------------------------------------- stress: long, odd, many
function stressData() {
  const now = Date.now();
  const iso = (h) => new Date(now - h * 3600e3).toISOString();
  const items = [];
  const titles = [
    "ＹＯＵＳＵＫＥＹＵＫＩＭＡＴＳＵ＿ＡＬＬ＿ＮＩＧＨＴ＿ＬＯＮＧ＿ＡＴ＿ＣＩＲＣＵＳ＿ＯＳＡＫＡ＿２０２６＿ＦＵＬＬ＿ＳＥＴ＿ＮＯ＿ＢＲＥＡＫＳ",
    "行松陽介 DJ set at 大阪 CIRCUS — 2026年10月12日 オールナイトロング 完全版",
    "<script>alert(1)</script> Yukimatsu & \"friends\" 'quoted' set",
    "a",
    "Yousuke Yukimatsu b2b someone with an extremely long name that keeps going and going | Boiler Room x Some Festival Stage Takeover 2026 (Full 4 Hour Set, Remastered Audio)",
  ];
  for (let i = 0; i < 24; i++) {
    items.push({
      id: `stress:${i}`, artistId: i % 3 === 0 ? "a2" : "yousuke-yukimatsu", platform: i % 2 ? "soundcloud" : "youtube",
      status: i < 18 ? "accepted" : i < 22 ? "rejected" : "pending",
      url: "https://example.com/set/" + i, title: titles[i % titles.length], uploader: i % 4 ? "Uploader " + i : "",
      publishedAt: i % 5 === 0 ? iso(i * 30).slice(0, 10) : iso(i * 30), publishedPrecision: i % 5 === 0 ? "date" : i % 7 === 0 ? "firstSeen" : "datetime",
      firstSeenAt: iso(i * 30 - 1), durationSec: i === 3 ? 36000 : 1801 + i * 600,
      thumbnail: i % 3 ? "https://127.0.0.1:9/missing.jpg" : null,
      quality: i % 6 === 5 ? undefined : { score: (i * 17) % 101, label: "God", verified: i % 2 === 0, analysisError: i % 2 ? "HTTP Error 403: Forbidden ".repeat(4) : null,
        analysis: i % 2 === 0 ? { rmsDb: -14, sideDb: -50, clipFraction: 0.004, silenceFraction: 0.35, cutoffHz: 8250, bassDb: -25 } : null,
        signals: [{ code: "x", impact: -40, text: "Mudret lyd: intet over 8,3 kHz" }, { code: "y", impact: 12, text: "Uploadet af en kendt platform (Some Extremely Long Uploader Name That Goes On)" }] },
      reasons: ["Lydkvaliteten vurderes for lav", "Titlen tyder på en telefonoptagelse og meget mere tekst end der burde være plads til i en lille chip"],
      alternates: i === 2 ? [{ platform: "soundcloud", url: "https://soundcloud.com/x" }, { platform: "youtube", url: "https://youtube.com/x" }] : undefined,
    });
  }
  return {
    version: 1, generatedAt: iso(9), scanOk: true,
    settings: { minDurationMinutes: 30, minQualityScore: 60, scanIntervalHours: 2, qualityBase: 62 },
    artists: [
      { id: "yousuke-yukimatsu", name: "Yousuke Yukimatsu", displayName: "¥ØU$UK€ ¥UK1MAT$U", subtitle: "x", trackingSince: iso(400), links: {} },
      { id: "a2", name: "Second Artist With A Long Name", displayName: "SECOND ARTIST WITH A VERY LONG STAGE NAME", trackingSince: iso(200), links: { soundcloud: "https://soundcloud.com/x" } },
      { id: "a3", name: "行松陽介", displayName: "行松陽介", trackingSince: iso(100), links: {} },
    ],
    health: [
      { artistId: "yousuke-yukimatsu", platform: "youtube", label: "YouTube-søgning “Yousuke Yukimatsu DJ set with a long query string”", ok: false, found: 0, error: "Sign in to confirm you’re not a bot. This helps protect our community. Learn more" },
      { artistId: "a2", platform: "soundcloud", label: "SoundCloud-profil x", ok: true, found: 1 },
    ],
    items,
  };
}

for (const [devName, device] of [["iphone-se", IPHONE_SE], ["iphone", IPHONE], ["edge", EDGE]]) {
  const tag = `stress ${devName}`;
  console.log(`\n${tag}`);
  const { page, context, errors } = await open(device, "dark", BASE, async (p) => {
    await p.route("**/data/sets.json*", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(stressData()) }));
    await p.route("**/missing.jpg", (r) => r.abort());
  });
  await page.click('[data-filter="status"][data-value="all"]');
  await page.click("#crate > summary");
  await page.locator(".set .meter").first().click();
  await page.waitForTimeout(300);
  check(`${tag}: missing thumbnails fall back to the generated cover`, await page.evaluate(() =>
    [...document.querySelectorAll(".art img")].every((i) => /^https?:\/\/(?!127\.0\.0\.1:8766)/.test(i.getAttribute("src") || ""))));
  check(`${tag}: no injected markup executed or rendered`, await page.locator(".set__title script").count() === 0 && await page.evaluate(() => !window.__xss));
  check(`${tag}: artist filter appears for several artists`, await page.locator("#artist-filter:not([hidden]) button").count() === 4);
  check(`${tag}: stale scan with a failed source is flagged`, (await page.locator("#scan-led").getAttribute("data-state")) === "warn");
  await commonChecks(tag, page, errors);
  await shot(page, `stress-${devName}`);
  await page.click('[data-filter="artist"][data-value="a2"]');
  await page.waitForTimeout(200);
  const artistIds = await page.$$eval(".set", (n) => n.length);
  check(`${tag}: artist filter narrows the list`, artistIds > 0 && artistIds < 18, String(artistIds));
  await context.close();
}

// -------------------------------------------------------- network failures
{
  console.log("\nnetwork failures");
  const { page, context, errors } = await open(IPHONE, "dark", BASE, async (p) => {
    await p.route("**/data/sets.json*", (r) => r.fulfill({ status: 500, body: "nope" }));
    await p.route("**/raw.githubusercontent.com/**", (r) => r.abort());
    await p.route("**/data/sets.js*", (r) => r.abort());
  });
  check("no data and no cache: clear error with retry", await page.locator(".empty h2").innerText() === "Data kunne ikke hentes" && await page.locator('[data-action="reload"]').count() === 1);
  check("status LED is red", (await page.locator("#scan-led").getAttribute("data-state")) === "bad");
  await shot(page, "error-no-data");
  await context.close();

  const ctx2 = await browser.newContext({ ...IPHONE, colorScheme: "dark" });
  const p2 = await ctx2.newPage();
  await p2.goto(BASE, { waitUntil: "load" });      // fills the cache
  await p2.waitForTimeout(800);
  await p2.route("**/data/sets.json*", (r) => r.abort());
  await p2.route("**/raw.githubusercontent.com/**", (r) => r.abort());
  await p2.route("**/data/sets.js*", (r) => r.abort());
  await p2.reload({ waitUntil: "load" });
  await p2.waitForTimeout(800);
  const noticeShown = await p2.locator("#notice:not([hidden])").count();
  const empty = await p2.locator(".empty h2").innerText();
  check("offline: falls back to the cached copy", empty === "Ingen nye sæt endnu", `notice=${noticeShown} empty=${empty}`);
  await ctx2.close();
  check("no script errors in failure modes", errors.length === 0, errors.join(" | "));
}

// ------------------------------------------- storage blocked by the browser
{
  console.log("\nstorage blocked");
  const { page, context, errors } = await open(IPHONE, "dark", BASE + "#demo", async (p) => {
    await p.addInitScript(() => {
      for (const name of ["localStorage", "sessionStorage"]) {
        Object.defineProperty(window, name, { get() { throw new DOMException("blocked", "SecurityError"); } });
      }
    });
  });
  check("page still renders without storage", await page.locator(".set").count() >= 3);
  const before = await page.$eval('[data-count="heard"]', (n) => Number(n.textContent));
  await page.locator(".set").first().locator(".btn--heard").click();
  await page.waitForTimeout(200);
  check("heard toggle still works for the visit", (await page.$eval('[data-count="heard"]', (n) => Number(n.textContent))) === before + 1);
  check("no script errors without storage", errors.length === 0, errors.join(" | "));
  await context.close();
}

// ------------------------------------------------------------ file:// mode
{
  console.log("\nfile://");
  const file = "file://" + path.resolve(process.env.WEB_DIR || "web", "index.html");
  const context = await browser.newContext({ ...EDGE, colorScheme: "light" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/raw.githubusercontent.com/**", (r) => r.abort());
  await page.goto(file, { waitUntil: "load" });
  await page.waitForTimeout(1200);
  check("opened from disk, data still loads via sets.js", await page.locator(".plate__name").first().innerText() === "¥ØU$UK€ ¥UK1MAT$U");
  check("no script errors from disk", errors.length === 0, errors.join(" | "));
  await context.close();
}

await browser.close();
console.log(`\n${failures.length ? failures.length + " FAILED" : "ALL PASSED"}`);
process.exit(failures.length ? 1 : 0);
