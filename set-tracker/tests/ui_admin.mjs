// Browser self-check for managing what is tracked (admin.js).
//
// Drives the "Overvågninger" sheet in Chromium against a stand-in for the
// GitHub API that behaves like the real one where it matters: tokens, file
// versions (sha), conflicting saves, missing permissions and workflow runs.
// Checks what the app actually writes to config/artists.json, byte for byte
// where special characters are involved.
//
//   cd set-tracker && python -m http.server 8766 --directory web &
//   node tests/ui_admin.mjs            (needs: npm i playwright)
//
// Env: BASE, SHOTS, CHROMIUM as for ui_check.mjs.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE || "http://127.0.0.1:8766/";
const SHOTS = process.env.SHOTS || "ui-shots";
fs.mkdirSync(SHOTS, { recursive: true });
// A fixed copy of the config, so editing artists in the app never breaks these tests.
const REAL_CONFIG = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "artists.json"), "utf8"));
const API = "https://api.github.com";
const REPO = "/repos/MarcusSkovbaek/Jarvis";
const CONTENTS = REPO + "/contents/set-tracker/config/artists.json";
const RUNS = REPO + "/actions/workflows/set-tracker.yml/runs";
const DISPATCH = REPO + "/actions/workflows/set-tracker.yml/dispatches";
const GOOD = "github_pat_good";

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
const EDGE = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || undefined,
  args: process.env.HTTPS_PROXY ? ["--proxy-server=https=" + process.env.HTTPS_PROXY.replace(/^https?:\/\//, "")] : [],
});

// ------------------------------------------------------------ fake GitHub

function fakeGitHub(options = {}) {
  const st = {
    config: JSON.stringify(options.config || REAL_CONFIG, null, 2) + "\n",
    sha: "blob-1", n: 1, commits: [], runs: [], calls: [],
    failPut: options.failPut ? { ...options.failPut } : null,
    conflictOnce: !!options.conflictOnce,
  };
  const cors = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "Authorization, Content-Type, If-Match, If-Modified-Since, If-None-Match, If-Unmodified-Since, X-GitHub-OTP, X-Requested-With",
    "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE",
    "access-control-expose-headers": "ETag, Link, x-ratelimit-limit, x-ratelimit-remaining, x-ratelimit-reset",
  };
  async function handler(route) {
    const req = route.request();
    const url = new URL(req.url());
    const method = req.method();
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    const auth = (await req.allHeaders())["authorization"] || "";
    st.calls.push(`${method} ${url.pathname}${url.search}`);
    const reply = (status, body, headers = {}) => route.fulfill({
      status, headers: { ...cors, "content-type": "application/json; charset=utf-8", ...headers },
      body: body === undefined ? "" : JSON.stringify(body),
    });
    if (options.offline) return route.abort("internetdisconnected");
    if (auth !== `Bearer ${options.token || GOOD}`) return reply(401, { message: "Bad credentials" });
    if (url.pathname === "/user") return reply(200, { login: "tester" });
    if (url.pathname === REPO) return reply(200, { full_name: "MarcusSkovbaek/Jarvis", default_branch: "main" });
    if (url.pathname === CONTENTS) {
      if (url.searchParams.get("ref") && url.searchParams.get("ref") !== "main" && method === "GET") return reply(404, { message: "No commit found for the ref" });
      if (method === "GET") return reply(200, { sha: st.sha, encoding: "base64", content: Buffer.from(st.config, "utf8").toString("base64").replace(/(.{60})/g, "$1\n") });
      if (method === "PUT") {
        const body = JSON.parse(req.postData() || "{}");
        st.lastPut = body;
        if (st.failPut && st.failPut.times-- > 0) return reply(st.failPut.status, { message: "Resource not accessible by personal access token" }, st.failPut.headers || {});
        if (st.conflictOnce) {
          st.conflictOnce = false;
          if (options.concurrent) {      // somebody else saves first
            const other = JSON.parse(st.config);
            options.concurrent(other);
            st.config = JSON.stringify(other, null, 2) + "\n";
            st.sha = "blob-" + ++st.n;
          }
          return reply(409, { message: `is at ${st.sha} but expected ${body.sha}` });
        }
        if (body.sha !== st.sha) return reply(409, { message: "sha does not match" });
        if (body.branch !== "main") return reply(422, { message: "Branch not found" });
        st.config = Buffer.from(body.content, "base64").toString("utf8");
        st.sha = "blob-" + ++st.n;
        const commit = "commit-" + st.n;
        st.commits.push({ message: body.message, text: st.config, commit });
        st.runs.unshift({ id: st.n, head_sha: commit, event: "push", status: "queued", polls: 0,
          created_at: new Date().toISOString(), html_url: `https://github.com/MarcusSkovbaek/Jarvis/actions/runs/${st.n}` });
        return reply(200, { content: { sha: st.sha }, commit: { sha: commit } });
      }
    }
    if (url.pathname === DISPATCH && method === "POST") {
      if (options.noActions) return reply(403, { message: "Resource not accessible by personal access token" });
      const body = JSON.parse(req.postData() || "{}");
      st.dispatchRef = body.ref;
      st.runs.unshift({ id: ++st.n, head_sha: "dispatch-" + st.n, event: "workflow_dispatch", status: "queued", polls: 0,
        created_at: new Date().toISOString(), html_url: `https://github.com/MarcusSkovbaek/Jarvis/actions/runs/${st.n}` });
      return route.fulfill({ status: 204, headers: cors });
    }
    if (url.pathname === RUNS) {
      const queued = [];
      for (const r of st.runs) {
        r.polls++;
        r.status = r.polls >= 3 ? "completed" : r.polls >= 2 ? "in_progress" : "queued";
        if (r.status === "completed" && !r.finished && options.cancelFirst && !st.cancelled) {
          // GitHub keeps one waiting run per queue: a newer one (here the schedule) cancels it.
          st.cancelled = true;
          r.finished = true;
          r.conclusion = "cancelled";
          queued.push({ id: ++st.n, head_sha: "sched-" + st.n, event: "schedule", status: "queued", polls: 0,
            created_at: new Date().toISOString(), html_url: `https://github.com/MarcusSkovbaek/Jarvis/actions/runs/${st.n}` });
        } else if (r.status === "completed" && !r.finished) {
          r.conclusion = options.conclusion || "success";
          r.finished = true;
          options.onRunDone && options.onRunDone(r);
        }
      }
      st.runs.unshift(...queued);
      const head = url.searchParams.get("head_sha"), ev = url.searchParams.get("event");
      return reply(200, { total_count: st.runs.length, workflow_runs: st.runs.filter((r) => (!head || r.head_sha === head) && (!ev || r.event === ev)) });
    }
    return reply(404, { message: "Not Found" });
  }
  return { st, handler, config: () => JSON.parse(st.config) };
}

// One accepted set, as the scanner writes it.
function acceptedItem(id) {
  return { id, artistId: REAL_CONFIG.artists[0].id, platform: "youtube", status: "accepted",
    url: "https://www.youtube.com/watch?v=" + id, title: "Yousuke Yukimatsu DJ set " + id, uploader: "Boiler Room",
    publishedAt: new Date(Date.now() - 3600e3).toISOString(), publishedPrecision: "datetime", firstSeenAt: new Date().toISOString(),
    durationSec: 5400, quality: { score: 80, label: "Meget god", verified: true, analysis: null, signals: [] } };
}

// The page's own data (sets.json), changeable while a test runs.
function siteData() {
  const d = {
    version: 1, generatedAt: new Date(Date.now() - 20 * 60e3).toISOString().replace(/\.\d+Z$/, "Z"), scanOk: true,
    settings: { minDurationMinutes: 30, minQualityScore: 60, scanIntervalHours: 2, qualityBase: 62 },
    artists: REAL_CONFIG.artists.map((a) => ({
      id: a.id, name: a.name, displayName: a.displayName, subtitle: a.subtitle || "", searchNames: a.searchNames,
      trackingSince: new Date(a.trackingSince).toISOString().replace(/\.\d+Z$/, "Z"), links: a.links,
      profiles: { soundcloud: a.sources.soundcloud.users || [], youtube: a.sources.youtube.channels || [] },
    })),
    problems: [], health: [
      { artistId: REAL_CONFIG.artists[0].id, platform: "youtube", label: "YouTube-søgning “x”", ok: true, found: 40,
        url: "https://www.youtube.com/results?search_query=x&sp=CAI%253D" },
      { artistId: REAL_CONFIG.artists[0].id, platform: "soundcloud", label: "SoundCloud-profil yousukeyukimatsu", ok: true, found: 0,
        url: "https://soundcloud.com/yousukeyukimatsu/tracks" }],
    items: [],
  };
  return d;
}

async function open(device, { gh, data, token, scheme = "dark", url = BASE } = {}) {
  const context = await browser.newContext({ ...device, colorScheme: scheme, locale: "da-DK", timezoneId: "Europe/Copenhagen", ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console: " + m.text()); });
  await page.addInitScript(([t]) => {
    window.SET_TRACKER_POLL_MS = 120;
    if (t) localStorage.setItem("saetradar:github:v1", JSON.stringify({ token: t, login: "tester" }));
  }, [token || null]);
  await page.route("**/raw.githubusercontent.com/**", (r) => r.abort());
  if (gh) await page.route(API + "/**", gh.handler);
  if (data) await page.route("**/data/sets.json*", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(data.current) }));
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => document.querySelector("#queue")?.getAttribute("aria-busy") === "false");
  await page.evaluate(() => document.fonts.ready);
  return { page, context, errors };
}

const sheetOpen = (page) => page.evaluate(() => document.getElementById("manage").open);
const title = (page) => page.locator("#manage-title").innerText();
async function settle(page, ms = 250) { await page.waitForTimeout(ms); }

async function sheetLayout(page) {
  return page.evaluate(() => {
    const out = [];
    const panel = document.querySelector(".sheet__panel").getBoundingClientRect();
    if (panel.right > window.innerWidth + 1 || panel.left < -1) out.push("sheet wider than the screen");
    const bodyEl = document.getElementById("manage-body");
    if (bodyEl.scrollWidth > bodyEl.clientWidth + 1) out.push(`sheet scrolls sideways (${bodyEl.scrollWidth} > ${bodyEl.clientWidth})`);
    for (const n of bodyEl.querySelectorAll("*")) {
      const r = n.getBoundingClientRect();
      if (!r.width || n.closest("[hidden]")) continue;
      if (r.right > panel.right + 1 || r.left < panel.left - 1) { out.push(`${n.tagName.toLowerCase()}.${n.className} sticks out`); break; }
    }
    for (const n of bodyEl.querySelectorAll("button, label, .monitor__name")) {
      const cs = getComputedStyle(n);
      if (n.closest("[hidden]") || cs.textOverflow === "ellipsis") continue;
      if (n.scrollWidth > n.clientWidth + 1 && cs.overflow !== "visible") out.push(`clipped: "${n.textContent.trim().slice(0, 30)}"`);
    }
    return out.slice(0, 8);
  });
}

// A datetime-local value is local time in the page's time zone (Copenhagen), not the test runner's.
const inputTime = (page, sel) => page.$eval(sel, (el) => new Date(el.value).getTime());

function localInput(date) {
  const p = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
}

// ---------------------------------------------------------------- tests

console.log("\nread-only without a token");
{
  const { page, context, errors } = await open(IPHONE, { data: { current: siteData() } });
  await page.click("#manage-open");
  await settle(page);
  check("the sheet opens from the top bar", await sheetOpen(page));
  check("it lists what is tracked", (await page.locator(".monitor__name").first().innerText()) === "¥ØU$UK€ ¥UK1MAT$U");
  check("it shows the start date", /Søger fra 4\. okt\. 2026, kl\.\s19\.50/.test(await page.locator(".monitor__meta").first().innerText()));
  check("nothing can be changed yet", await page.locator("[data-act=edit], [data-act=add]").count() === 0);
  check("it offers to connect GitHub", await page.locator("[data-act=connect-view]").count() === 1);
  check("sheet layout holds on an iPhone", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  await page.screenshot({ path: path.join(SHOTS, "admin-readonly-iphone.png") });
  await page.keyboard.press("Escape");
  await settle(page);
  check("Escape closes the sheet", !(await sheetOpen(page)));
  check("focus goes back to the button that opened it", await page.evaluate(() => document.activeElement && document.activeElement.id === "manage-open"));
  // The start date in the plate opens the (read-only) list without a token.
  await page.click(".criteria__edit");
  await settle(page);
  check("the start date in the plate opens the sheet", await sheetOpen(page) && (await title(page)) === "Overvågninger");
  check("no script errors (read-only)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nconnecting GitHub");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() } });
  await page.click("#manage-open");
  await page.click("[data-act=connect-view]");
  await settle(page);
  check("the connect view explains the token", /Only select repositories/.test(await page.locator(".steps").innerText()));
  await page.screenshot({ path: path.join(SHOTS, "admin-connect-iphone.png") });
  await page.click("#f-connect");
  await settle(page);
  check("an empty token is refused with a message", /Sæt tokenet ind/.test(await page.locator("#manage-body").innerText()));
  await page.fill("#f-token", "github_pat_wrong");
  await page.click("#f-connect");
  await settle(page, 400);
  check("a wrong token is refused", /afviste tokenet/.test(await page.locator("#manage-body").innerText()));
  check("a wrong token is not stored", await page.evaluate(() => localStorage.getItem("saetradar:github:v1")) === null);
  check("the typed token is kept for another try", (await page.inputValue("#f-token")) === "github_pat_wrong");
  await page.fill("#f-token", "  " + GOOD + "  ");
  await page.click("#f-connect");
  await page.waitForSelector(".sheet__account");
  check("a good token connects", /Forbundet som tester/.test(await page.locator(".sheet__account").innerText()));
  const saved = JSON.parse(await page.evaluate(() => localStorage.getItem("saetradar:github:v1")) || "{}");
  check("the token is stored trimmed in this browser only", saved.token === GOOD && saved.login === "tester");
  await page.waitForSelector("[data-act=edit]");
  check("connected: monitors can be edited and added", await page.locator("[data-act=edit]").count() === 1 && await page.locator("[data-act=add]").count() === 1);
  check("only GitHub's API was contacted", gh.st.calls.every((c) => /^(GET|PUT|POST) \//.test(c)));
  await page.click("[data-act=disconnect]");
  await settle(page);
  check("disconnect forgets the token", await page.evaluate(() => localStorage.getItem("saetradar:github:v1")) === null
    && await page.locator("[data-act=connect-view]").count() === 1);
  check("no script errors (connect)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nadding a monitor");
{
  const data = { current: siteData() };
  const gh = fakeGitHub({ onRunDone: () => { data.current = siteData(); data.current.generatedAt = new Date().toISOString(); } });
  const { page, context, errors } = await open(IPHONE, { gh, data, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=add]:not([disabled])");
  await page.click("[data-act=add]");
  await page.waitForSelector("#f-name");
  check("the form opens with the name field focused", await page.evaluate(() => document.activeElement.id === "f-name"));
  check("a new monitor starts from now", Math.abs((await inputTime(page, "#f-since")) - Date.now()) < 120e3);
  await page.screenshot({ path: path.join(SHOTS, "admin-form-empty-iphone.png") });

  // Validation first: nothing may be written.
  await page.click("#f-save");
  await settle(page);
  check("an empty name is refused at the field", /Skriv kunstnerens navn/.test(await page.locator("[data-field=name] .field__error").innerText())
    && await page.evaluate(() => document.activeElement.id === "f-name"));
  await page.fill("#f-name", "yousuke yukimatsu");
  await page.fill("#f-soundcloud", "not a profile!");
  await page.fill("#f-youtube", "youtube.com/watch?v=abc");
  await page.fill("#f-since", localInput(new Date(Date.now() + 400 * 864e5)));
  await page.click("#f-save");
  await settle(page);
  const fieldErrors = await page.$$eval(".field__error:not([hidden])", (ns) => ns.map((n) => n.closest("[data-field]").dataset.field));
  check("bad profile, channel and far-future date are refused", ["soundcloud", "youtube", "since"].every((k) => fieldErrors.includes(k)), fieldErrors.join(","));
  check("what was typed survives a refused save", (await page.inputValue("#f-name")) === "yousuke yukimatsu");
  check("nothing was written while refused", gh.st.commits.length === 0);
  await page.fill("#f-soundcloud", "");
  await page.fill("#f-youtube", "");
  await page.fill("#f-since", localInput(new Date()));
  await page.click("#f-save");
  await settle(page, 500);
  check("a second monitor of the same artist is refused", /findes allerede/.test(await page.locator("#f-error").innerText()) && gh.st.commits.length === 0);
  await page.screenshot({ path: path.join(SHOTS, "admin-form-errors-iphone.png") });

  // A proper new monitor, with special characters everywhere.
  await page.fill("#f-name", "Kin Ofhis  Sound");
  await page.fill("#f-display", "K1N 0FH1$ $ØUND™");
  await page.fill("#f-extra", "キン・オフィス");
  await page.press("#f-extra", "Enter");
  await page.fill("#f-extra", "Kin Of His Sound");
  await page.click("[data-act=add-name]");
  await page.fill("#f-extra", "KIN OF HIS SOUND");   // same as the last one but in capitals: ignored
  await page.press("#f-extra", "Enter");
  await page.fill("#f-extra", "🏝️ Kin");             // typed but not added with the button: still counts
  const chips = await page.$$eval("#f-names-list .chip > span", (ns) => ns.map((n) => n.textContent));
  check("spellings become chips, repeats ignored", JSON.stringify(chips) === JSON.stringify(["キン・オフィス", "Kin Of His Sound"]), chips.join(" | "));
  await page.click("#f-names-list .chip:nth-child(2) .chip__x");
  check("a chip can be removed", (await page.$$eval("#f-names-list .chip", (ns) => ns.length)) === 1);
  await page.fill("#f-extra", "🏝️ Kin");
  await page.click("[data-act=preset][data-days='30']");
  const since = await inputTime(page, "#f-since");
  check("the 30-day shortcut sets the date", Math.abs((Date.now() - since) / 864e5 - 30) < 0.01);
  await page.fill("#f-soundcloud", "https://soundcloud.com/KinOfHisSound/sets/live");
  await page.fill("#f-youtube", "@kinofhissound");
  await page.screenshot({ path: path.join(SHOTS, "admin-form-filled-iphone.png") });
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  const cfg = gh.config();
  const added = cfg.artists.find((a) => a.id === "kin-ofhis-sound");
  check("one commit was made", gh.st.commits.length === 1, String(gh.st.commits.length));
  check("the commit message names the artist", gh.st.commits[0].message === "Sætradar: overvåg Kin Ofhis Sound", gh.st.commits[0].message);
  check("the new monitor has a clean id and name", !!added && added.name === "Kin Ofhis Sound");
  check("special characters are written exactly", added && added.displayName === "K1N 0FH1$ $ØUND™"
    && JSON.stringify(added.searchNames) === JSON.stringify(["Kin Ofhis Sound", "K1N 0FH1$ $ØUND™", "キン・オフィス", "🏝️ Kin"]),
    added && JSON.stringify(added.searchNames));
  check("the start date is stored in UTC", added && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(added.trackingSince)
    && Math.abs(new Date(added.trackingSince) - since) < 60e3, added && added.trackingSince);
  check("profiles are normalised", added && JSON.stringify(added.sources) === JSON.stringify({
    youtube: { searchQueries: [], channels: ["https://www.youtube.com/@kinofhissound"] },
    soundcloud: { searchQueries: [], users: ["kinofhissound"] } }), added && JSON.stringify(added.sources));
  check("links point to the profiles", added && added.links.soundcloud === "https://soundcloud.com/kinofhissound"
    && added.links.youtube === "https://www.youtube.com/@kinofhissound");
  const before = REAL_CONFIG.artists[0], after = cfg.artists.find((a) => a.id === before.id);
  check("the existing monitor is untouched", JSON.stringify(after) === JSON.stringify(before));
  check("settings and trusted uploaders are untouched", JSON.stringify(cfg.settings) === JSON.stringify(REAL_CONFIG.settings)
    && JSON.stringify(cfg.trustedUploaders) === JSON.stringify(REAL_CONFIG.trustedUploaders));
  check("the file stays readable JSON with a final newline", gh.st.config.endsWith("}\n") && gh.st.config.includes('\n  "artists": ['));
  // Following the scan GitHub starts.
  check("the top bar shows the scan is running", /Scanner/.test(await page.locator("#scan-text").innerText())
    && (await page.locator("#scan-led").getAttribute("data-state")) === "busy");
  check("the new monitor waits for its first scan", /venter på første scanning/i.test(await page.locator("#manage-body").innerText()));
  await page.waitForFunction(() => /Opdateret/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("the scan is followed to the end", true);
  check("the page reloaded the new data", !/Scanner/.test(await page.locator("#scan-text").innerText()));
  check("the run can be opened on GitHub", (await page.getAttribute(".sheet__follow a", "href")).startsWith("https://github.com/MarcusSkovbaek/Jarvis/actions/runs/"));
  check("sheet layout holds after saving", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  await page.screenshot({ path: path.join(SHOTS, "admin-after-save-iphone.png") });
  check("no script errors (add)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nmoving the start date from the plate");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(EDGE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click(".criteria__edit");
  await page.waitForSelector("#f-since");
  check("the plate's start date opens the form at the date", await page.evaluate(() => document.activeElement.id === "f-since"));
  check("the form shows the artist's own values", (await page.inputValue("#f-name")) === "Yousuke Yukimatsu"
    && (await page.inputValue("#f-display")) === "¥ØU$UK€ ¥UK1MAT$U" && (await page.inputValue("#f-soundcloud")) === "yousukeyukimatsu");
  check("the other spellings are chips", JSON.stringify(await page.$$eval("#f-names-list .chip > span", (ns) => ns.map((n) => n.textContent)))
    === JSON.stringify(["YØU$UK€ YUK1MAT$U", "Yosuke Yukimatsu", "行松陽介"]));
  check("the date shows the stored start in local time", (await page.inputValue("#f-since")) === "2026-10-04T19:50");
  const echo = () => page.locator("#f-since-echo").textContent();
  check("the chosen moment is spelled out in Danish", /^søndag den 4\. oktober 2026 kl\. 19\.50$/i.test(await echo()), await echo());
  await page.screenshot({ path: path.join(SHOTS, "admin-edit-desktop.png") });
  await page.fill("#f-since", "2026-09-01T00:00");
  check("the Danish line follows the field", /^tirsdag den 1\. september 2026 kl\. 00\.00$/i.test(await echo()), await echo());
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  const after = gh.config().artists[0], before = REAL_CONFIG.artists[0];
  check("the new start is saved (1 Sep, Copenhagen time)", after.trackingSince === "2026-08-31T22:00:00Z", after.trackingSince);
  const same = Object.keys(before).filter((k) => k !== "trackingSince").every((k) => JSON.stringify(before[k]) === JSON.stringify(after[k]));
  check("nothing else about the monitor changed", same && Object.keys(after).length === Object.keys(before).length);
  check("the commit says the start moved", /søger fra 1\. sep\. 2026, kl\. 00\.00\)$/.test(gh.st.commits[0].message), gh.st.commits[0].message);
  check("sheet layout holds on desktop", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  check("no script errors (start date)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nsomebody else saved in between");
{
  const gh = fakeGitHub({
    conflictOnce: true,
    concurrent: (cfg) => cfg.artists.push({ id: "other", name: "Other", trackingSince: "2026-10-01T00:00:00Z",
      searchNames: ["Other"], sources: { youtube: {}, soundcloud: {} } }),
  });
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-since");
  await page.fill("#f-since", "2026-10-01T12:00");
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  const cfg = gh.config();
  check("the save is redone on top of the other change", cfg.artists.length === 2 && cfg.artists[1].id === "other"
    && cfg.artists[0].trackingSince === "2026-10-01T10:00:00Z", JSON.stringify(cfg.artists.map((a) => [a.id, a.trackingSince])));
  check("no script errors (conflict)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nremoving a monitor");
{
  const two = JSON.parse(JSON.stringify(REAL_CONFIG));
  two.artists.push({ id: "dj-x", name: "DJ X", displayName: "DJ X", trackingSince: "2026-10-01T00:00:00Z",
    searchNames: ["DJ X"], aliases: [], links: {}, sources: { youtube: { channels: [] }, soundcloud: { users: [] } } });
  const gh = fakeGitHub({ config: two });
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit][data-id=dj-x]");
  check("monitors not scanned yet are marked", /venter på første scanning/i.test(await page.locator(".monitor").nth(1).innerText()));
  await page.click("[data-act=edit][data-id=dj-x]");
  await page.waitForSelector("#f-remove");
  check("on a phone, editing does not bring up the keyboard", await page.evaluate(() => document.activeElement.id === "manage-title"));
  await page.click("#f-remove");
  await settle(page);
  check("the first tap only arms the button", /Tryk igen/.test(await page.locator("#f-remove").innerText()) && gh.st.commits.length === 0);
  await page.click("#f-remove");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  check("the second tap removes it", JSON.stringify(gh.config().artists.map((a) => a.id)) === JSON.stringify(["yousuke-yukimatsu"]));
  check("the commit says so", gh.st.commits[0].message === "Sætradar: stop med at overvåge DJ X");
  check("no script errors (remove)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nGitHub says no");
{
  const gh = fakeGitHub({ failPut: { status: 403, times: 1 } });
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-since");
  await page.fill("#f-since", "2026-10-02T12:00");
  await page.click("#f-save");
  await page.waitForFunction(() => /ikke lov/.test(document.querySelector("#f-error")?.textContent || ""));
  check("a missing permission is explained in the form", true);
  check("the form is still there with the change", (await page.inputValue("#f-since")) === "2026-10-02T12:00");
  check("the save button works again", !(await page.locator("#f-save").isDisabled()));
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  check("a second try goes through", gh.config().artists[0].trackingSince === "2026-10-02T10:00:00Z");
  check("no script errors (403)", errors.length === 0, errors.join(" | "));
  await context.close();

  const gh2 = fakeGitHub({ failPut: { status: 403, times: 1, headers: { "x-ratelimit-remaining": "0" } } });
  const r2 = await open(IPHONE, { gh: gh2, data: { current: siteData() }, token: GOOD });
  await r2.page.click("#manage-open");
  await r2.page.waitForSelector("[data-act=edit]");
  await r2.page.click("[data-act=edit]");
  await r2.page.waitForSelector("#f-save");
  await r2.page.click("#f-save");
  await r2.page.waitForFunction(() => /pause/.test(document.querySelector("#f-error")?.textContent || ""));
  check("a rate limit is told apart from a permission problem", true);
  await r2.context.close();

  // A token that expired since it was saved.
  const gh3 = fakeGitHub({ token: "github_pat_new" });
  const r3 = await open(IPHONE, { gh: gh3, data: { current: siteData() }, token: GOOD });
  await r3.page.click("#manage-open");
  await r3.page.waitForSelector("#f-token");
  check("an expired token sends you to connect again", /afviste tokenet/.test(await r3.page.locator("#manage-body").innerText())
    && await r3.page.evaluate(() => localStorage.getItem("saetradar:github:v1")) === null);
  check("no script errors (expired)", r3.errors.length === 0, r3.errors.join(" | "));
  await r3.context.close();

  // No connection to GitHub at all.
  const gh4 = fakeGitHub({ offline: true });
  const r4 = await open(IPHONE, { gh: gh4, data: { current: siteData() }, token: GOOD });
  await r4.page.click("#manage-open");
  await r4.page.waitForFunction(() => /Ingen forbindelse til GitHub/.test(document.querySelector("#manage-body").textContent));
  check("no connection to GitHub is explained", true);
  check("the list still shows what is tracked", (await r4.page.locator(".monitor__name").count()) === 1);
  check("no script errors (offline)", r4.errors.length === 0, r4.errors.join(" | "));
  await r4.context.close();
}

console.log("\nscan now");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(EDGE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=scan-now]:not([disabled])");
  await page.click("[data-act=scan-now]");
  await page.waitForFunction(() => /Opdateret|Færdig/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("a scan can be started and followed", gh.st.dispatchRef === "main");
  check("it ran on the default branch", gh.st.calls.some((c) => c.startsWith("POST " + DISPATCH)));
  check("no script errors (scan now)", errors.length === 0, errors.join(" | "));
  await context.close();

  const gh2 = fakeGitHub({ noActions: true });
  const r2 = await open(EDGE, { gh: gh2, data: { current: siteData() }, token: GOOD });
  await r2.page.click("#manage-open");
  await r2.page.waitForSelector("[data-act=scan-now]:not([disabled])");
  await r2.page.click("[data-act=scan-now]");
  await r2.page.waitForFunction(() => /Actions: Read and write/.test(document.querySelector("#manage-body").textContent));
  check("a token without Actions access is explained", true);
  await r2.context.close();
}

console.log("\nkeyboard focus and typing");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(EDGE, { gh, data: { current: siteData() }, token: GOOD });
  const active = () => page.evaluate(() => {
    const a = document.activeElement;
    return a ? (a.id || a.getAttribute("data-act") || a.className || a.tagName) : "none";
  });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.focus("[data-act=edit]");
  await page.keyboard.press("Enter");
  await page.waitForSelector("#f-names-list .chip");
  check("the form opens with the name field focused (keyboard)", (await active()) === "f-name", await active());
  // Removing a spelling hands focus to the next one, then to the field.
  await page.focus("#f-names-list .chip:nth-child(2) .chip__x");
  await page.keyboard.press("Enter");
  check("removing a chip focuses the next chip", (await active()) === "drop-name"
    && (await page.$$eval("#f-names-list .chip", (ns) => ns.length)) === 2, await active());
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  check("removing the last chip focuses the spelling field", (await active()) === "f-extra", await active());
  // Enter that confirms a Japanese word being typed is not "add".
  await page.fill("#f-extra", "ゆきまつ");
  await page.dispatchEvent("#f-extra", "keydown", { key: "Enter", isComposing: true });
  check("Enter during Japanese input does not add a chip", (await page.$$eval("#f-names-list .chip", (ns) => ns.length)) === 0
    && (await page.inputValue("#f-extra")) === "ゆきまつ");
  await page.press("#f-extra", "Enter");
  check("a plain Enter adds it", (await page.$$eval("#f-names-list .chip > span", (ns) => ns.map((n) => n.textContent))).join() === "ゆきまつ");
  // Saving by keyboard: focus stays in the sheet and lands on its title.
  await page.focus("#f-save");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  await settle(page);
  check("after saving, focus is on the sheet's title", (await active()) === "manage-title", await active());
  check("the title shows no focus ring", await page.$eval("#manage-title", (el) => getComputedStyle(el).outlineStyle) === "none");
  // Disabled buttons look disabled.
  check("Scan nu stays available while a scan runs", await page.$eval("[data-act=scan-now]", (el) => !el.disabled));
  check("a disabled button looks disabled", await page.$eval("[data-act=add]", (el) => {
    el.disabled = true; const o = getComputedStyle(el).opacity; el.disabled = false; return o === "0.5";
  }));
  await page.waitForFunction(() => /Opdateret/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("the result says how many sets are new", /Opdateret \d{2}\.\d{2}: ingen nye sæt\./.test(await page.locator(".sheet__follow").innerText()),
    await page.locator(".sheet__follow").innerText());
  check("no script errors (focus)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\na form opened from an older copy of the file");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.keyboard.press("Escape");
  await settle(page);
  // Meanwhile another device adds a spelling and moves the start date.
  const other = JSON.parse(gh.st.config);
  other.artists[0].searchNames.push("ユウスケ");
  other.artists[0].trackingSince = "2026-09-20T08:00:00Z";
  gh.st.config = JSON.stringify(other, null, 2) + "\n";
  gh.st.sha = "blob-" + ++gh.st.n;
  await page.click(".criteria__edit");
  await page.waitForFunction(() => document.querySelector("#f-since")?.value === "2026-09-20T10:00");
  check("the form shows the newer file", (await page.$$eval("#f-names-list .chip > span", (ns) => ns.map((n) => n.textContent))).includes("ユウスケ"));
  check("focus stays at the date", await page.evaluate(() => document.activeElement.id === "f-since"));
  await page.fill("#f-since", "2026-09-15T08:00");
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  const saved = gh.config().artists[0];
  check("saving keeps the other device's spelling", saved.searchNames.includes("ユウスケ") && saved.trackingSince === "2026-09-15T06:00:00Z",
    JSON.stringify([saved.searchNames, saved.trackingSince]));
  check("no script errors (older copy)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\na scan replaced by a newer one in GitHub's queue");
{
  const data = { current: siteData() };
  const gh = fakeGitHub({ cancelFirst: true, onRunDone: () => {
    data.current = siteData();
    data.current.generatedAt = new Date().toISOString();
    data.current.items = [acceptedItem("new1")];
    data.current.artists[0].backlog = 7;
  } });
  const { page, context, errors } = await open(IPHONE, { gh, data, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-since");
  await page.fill("#f-since", "2026-09-01T00:00");
  await page.click("#f-save");
  await page.waitForFunction(() => /Venter på den næste scanning/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("a cancelled run hands over to the next one", true);
  await page.waitForFunction(() => /Opdateret/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  const text = await page.locator(".sheet__follow").innerText();
  check("the newer run is followed to the end", /1 nyt sæt/.test(text), text);
  check("sets still waiting are mentioned", /7 ældre uploads vurderes ved næste scanning/.test(text), text);
  check("the run link points to the newer run", (await page.getAttribute(".sheet__follow a", "href")).endsWith("/runs/" + gh.st.runs[0].id));
  check("the list says the search goes on", /Søger stadig længere tilbage: 7 uploads/.test(await page.locator(".monitor").first().innerText()));
  check("sheet layout holds with the note", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  await page.screenshot({ path: path.join(SHOTS, "admin-backlog-iphone.png") });
  await page.keyboard.press("Escape");
  await settle(page);
  await page.screenshot({ path: path.join(SHOTS, "page-backlog-iphone.png") });
  check("the page says so too", /Søger stadig længere tilbage/.test(await page.locator("#notice").innerText())
    && await page.locator("#notice").isVisible());
  check("no script errors (cancelled)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nthe dimmed backdrop");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(EDGE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-name");
  // Selecting text in a field and letting go outside the sheet.
  const box = await page.locator("#f-name").boundingBox();
  await page.mouse.move(box.x + box.width - 10, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(12, 12, { steps: 4 });
  await page.mouse.up();
  await settle(page);
  check("a text selection ending outside keeps the sheet open", await sheetOpen(page));
  await page.mouse.click(12, 12);
  await settle(page);
  check("a click on the backdrop closes it", !(await sheetOpen(page)));
  check("no script errors (backdrop)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nscanning from the page");
{
  const data = { current: siteData() };
  const gh = fakeGitHub({ onRunDone: () => { data.current = siteData(); data.current.generatedAt = new Date().toISOString(); } });
  const { page, context, errors } = await open(IPHONE, { gh, data, token: GOOD });
  check("connected, the refresh button scans", (await page.getAttribute("#refresh", "aria-label")) === "Scan nu");
  check("the bottom of the page offers Scan nu", await page.locator("#scan-row").isVisible()
    && /også hvis en anden kører/.test(await page.locator("#scan-hint").innerText()));
  await page.click("#refresh");
  await page.waitForFunction(() => /Scanner/.test(document.querySelector("#scan-text").textContent));
  check("the top bar starts a scan on GitHub", gh.st.calls.filter((c) => c.startsWith("POST " + DISPATCH)).length === 1);
  check("without opening the sheet", !(await sheetOpen(page)));
  check("a message says it started", /Scanning startet/.test(await page.locator("#toast-text").innerText()));
  await page.waitForFunction(() => /Opdateret \d{2}\.\d{2}: ingen nye sæt/.test(document.querySelector("#toast-text").textContent), null, { timeout: 15000 });
  check("the result is told when it is done", true);
  check("the top bar is back to normal", !/Scanner/.test(await page.locator("#scan-text").innerText()));
  // A second scan while one runs is queued, not refused.
  await page.click("[data-action=scan-now]");
  await page.waitForFunction(() => /Scanner/.test(document.querySelector("#scan-text").textContent));
  await page.click("[data-action=scan-now]");
  await page.waitForFunction(() => /i kø/.test(document.querySelector("#toast-text").textContent));
  check("Scan nu during a scan queues another", gh.st.calls.filter((c) => c.startsWith("POST " + DISPATCH)).length === 3);
  await page.waitForFunction(() => /Opdateret/.test(document.querySelector("#toast-text").textContent), null, { timeout: 20000 });
  check("no script errors (scan from the page)", errors.length === 0, errors.join(" | "));
  await context.close();

  // Not connected: refresh only reloads; Scan nu asks to connect, then scans.
  const gh2 = fakeGitHub();
  const r2 = await open(IPHONE, { gh: gh2, data: { current: siteData() } });
  check("not connected, the refresh button reloads", (await r2.page.getAttribute("#refresh", "aria-label")) === "Hent nyeste data");
  await r2.page.click("#refresh");
  await settle(r2.page, 400);
  check("and starts nothing on GitHub", gh2.st.calls.length === 0 && !(await sheetOpen(r2.page)));
  await r2.page.click("[data-action=scan-now]");
  await r2.page.waitForSelector("#f-token");
  check("Scan nu explains that GitHub is needed", /Scanningerne kører på GitHub/.test(await r2.page.locator(".sheet__lead").innerText()));
  await r2.page.fill("#f-token", GOOD);
  await r2.page.click("#f-connect");
  await r2.page.waitForFunction(() => /Venter på GitHub|I kø|Scanner|Opdateret/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("after connecting, the scan starts by itself", gh2.st.calls.filter((c) => c.startsWith("POST " + DISPATCH)).length === 1);
  check("the refresh button now scans", (await r2.page.getAttribute("#refresh", "aria-label")) === "Scan nu");
  check("no script errors (connect to scan)", r2.errors.length === 0, r2.errors.join(" | "));
  await r2.context.close();
}

console.log("\nfilters for names that are also words");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(IPHONE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-more");
  check("the filters are folded away when not in use", !(await page.$eval("#f-more", (d) => d.open)));
  await page.click("#f-more summary");
  await page.fill("#f-must", Array.from({ length: 26 }, (_, i) => "ord" + i).join(", "));
  await page.click("#f-save");
  await settle(page);
  check("too many words are refused at the field", /Højst 25/.test(await page.locator("[data-field=must] .field__error").innerText())
    && gh.st.commits.length === 0);
  await page.fill("#f-must", " Sub Focus ,drum and bass;  dnb , sub focus ");
  check("correcting the field clears its error", await page.$eval("[data-field=must] .field__error", (e) => e.hidden)
    && !(await page.$eval("#f-must", (e) => e.hasAttribute("aria-invalid"))));
  await page.fill("#f-exclude", "praise; church");
  await page.screenshot({ path: path.join(SHOTS, "admin-filters-iphone.png") });
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  let saved = gh.config().artists[0];
  check("filters are saved as lists of words", JSON.stringify(saved.mustMention) === JSON.stringify(["Sub Focus", "drum and bass", "dnb"])
    && JSON.stringify(saved.exclude) === JSON.stringify(["praise", "church"]), JSON.stringify([saved.mustMention, saved.exclude]));
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-more");
  check("in use, they are shown unfolded", await page.$eval("#f-more", (d) => d.open) && (await page.inputValue("#f-must")) === "Sub Focus, drum and bass, dnb");
  check("the form fits a phone with them open", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  await page.fill("#f-must", "");
  await page.fill("#f-exclude", "");
  await page.click("#f-save");
  await page.waitForFunction(() => document.querySelector("#manage-title").textContent === "Overvågninger");
  saved = gh.config().artists[0];
  check("emptied filters leave the file", !("mustMention" in saved) && !("exclude" in saved));
  check("no script errors (filters)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nopening the other searches");
{
  const { page, context, errors } = await open(IPHONE, { data: { current: siteData() } });
  const chips = page.locator(".spell");
  check("each spelling is a button", (await chips.count()) === REAL_CONFIG.artists[0].searchNames.length);
  await chips.nth(1).click();
  await page.waitForSelector("#spell-open");
  const links = await page.$$eval("#spell-open a", (as) => as.map((a) => [a.textContent.trim(), a.href, a.target]));
  check("a spelling opens its search on YouTube or SoundCloud", links.length === 2
    && links[0][1] === "https://www.youtube.com/results?search_query=%C2%A5%C3%98U%24UK%E2%82%AC+%C2%A5UK1MAT%24U&sp=CAI%253D"
    && links[1][1] === "https://soundcloud.com/search/sounds?q=%C2%A5%C3%98U%24UK%E2%82%AC%20%C2%A5UK1MAT%24U"
    && links.every((l) => l[2] === "_blank"), JSON.stringify(links));
  check("the chip says it is open", (await chips.nth(1).getAttribute("aria-expanded")) === "true");
  check("focus stays on the chip", await page.evaluate(() => document.activeElement.classList.contains("spell")));
  check("the page fits with it open", (await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)));
  await page.screenshot({ path: path.join(SHOTS, "page-spelling-open-iphone.png") });
  await chips.nth(1).click();
  check("tapping again closes it", (await page.locator("#spell-open").count()) === 0);
  const src = await page.$$eval("#sources a", (as) => as.map((a) => [a.href, a.target]));
  check("the sources at the bottom link to their searches", src.length === 2
    && src[0][0] === "https://www.youtube.com/results?search_query=x&sp=CAI%253D" && src.every((s) => s[1] === "_blank"), JSON.stringify(src));
  check("no script errors (searches)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\npressing Scan nu twice");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(EDGE, { gh, data: { current: siteData() }, token: GOOD });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=scan-now]:not([disabled])");
  await page.evaluate(() => { const b = document.querySelector("[data-act=scan-now]"); b.click(); b.click(); });
  await page.waitForFunction(() => /Opdateret|Færdig/.test(document.querySelector(".sheet__follow")?.textContent || ""), null, { timeout: 15000 });
  check("only one scan is started", gh.st.calls.filter((c) => c.startsWith("POST " + DISPATCH)).length === 1,
    String(gh.st.calls.filter((c) => c.startsWith("POST " + DISPATCH)).length));
  check("no script errors (twice)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nsmall screens and the light theme");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(IPHONE_SE, { gh, data: { current: siteData() }, token: GOOD, scheme: "light" });
  await page.click("#manage-open");
  await page.waitForSelector("[data-act=edit]");
  check("list fits a 320 px screen", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  await page.screenshot({ path: path.join(SHOTS, "admin-list-se-light.png") });
  await page.click("[data-act=edit]");
  await page.waitForSelector("#f-since");
  check("form fits a 320 px screen", (await sheetLayout(page)).length === 0, (await sheetLayout(page)).join(" | "));
  const tall = await page.evaluate(() => { const b = document.getElementById("manage-body"); return b.scrollHeight > b.clientHeight; });
  check("a long form scrolls inside the sheet", tall);
  await page.screenshot({ path: path.join(SHOTS, "admin-form-se-light.png"), fullPage: false });
  check("no script errors (small)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nexample data");
{
  const gh = fakeGitHub();
  const { page, context, errors } = await open(IPHONE, { gh, url: BASE + "#demo" });
  await page.click("#manage-open");
  await settle(page);
  check("the example view is read-only", /eksempelvisningen/.test(await page.locator("#manage-body").innerText())
    && await page.locator("[data-act=connect-view], [data-act=edit]").count() === 0);
  check("the example view never calls GitHub", gh.st.calls.length === 0);
  check("no script errors (demo)", errors.length === 0, errors.join(" | "));
  await context.close();
}

console.log("\nno monitors at all");
{
  const empty = siteData();
  empty.artists = [];
  const { page, context, errors } = await open(IPHONE, { data: { current: empty } });
  check("the page invites to add one", (await page.locator("[data-manage=add]").count()) >= 1
    && /Ingen kunstnere/.test(await page.locator(".plate__name").innerText()));
  await page.locator("[data-manage=add]").first().click();
  await settle(page);
  check("adding without a token asks to connect first", (await title(page)) === "Forbind GitHub");
  check("no script errors (none)", errors.length === 0, errors.join(" | "));
  await context.close();
}

await browser.close();
console.log(`\n${failures.length ? failures.length + " FAILED" : "ALL PASSED"}`);
process.exit(failures.length ? 1 : 0);
