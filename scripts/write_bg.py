
from pathlib import Path

CONTENT = '''// Azmuth bridge extension. Works in any Chromium browser.
// Full chrome.tabs API surface.

const BRIDGE_URL = "ws://localhost:9280";
const RECONNECT_ALARM = "azmuth-reconnect";

let socket = null;
let browserName = "unknown";

function detectBrowser() {
  const ua = (navigator.userAgent || "").toLowerCase();
  if (ua.includes("edg/")) return "edge";
  if (ua.includes("brave")) return "brave";
  if (ua.includes("opr/") || ua.includes("opera")) return "opera";
  if (ua.includes("vivaldi")) return "vivaldi";
  if (ua.includes("chrome")) return "chrome";
  if (ua.includes("firefox")) return "firefox";
  return "unknown";
}

browserName = detectBrowser();

function connect() {
  if (socket && socket.readyState === WebSocket.OPEN) return;
  try {
    socket = new WebSocket(BRIDGE_URL);
  } catch (e) {
    socket = null;
    return;
  }
  socket.onopen = () => {
    try {
      socket.send(JSON.stringify({
        type: "hello", browser: browserName, version: "1.0.0",
      }));
    } catch {}
  };
  socket.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (!msg || !msg.id || !msg.action) return;
    let result;
    try { result = await execute(msg); }
    catch (err) { result = { error: String(err) }; }
    try { socket.send(JSON.stringify({ id: msg.id, result })); } catch {}
  };
  socket.onclose = () => { socket = null; };
  socket.onerror = () => { socket = null; };
}

chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === RECONNECT_ALARM) connect(); });
connect();
setInterval(connect, 5000);

function serializeTab(t) {
  return {
    id: t.id, title: t.title, url: t.url,
    pinned: t.pinned, muted: t.mutedInfo?.muted || false,
    active: t.active, windowId: t.windowId, index: t.index,
  };
}

async function execute(cmd) {
  switch (cmd.action) {
    case "list": {
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.map(serializeTab) };
    }
    case "close": {
      const ids = Array.isArray(cmd.tab_ids)
        ? cmd.tab_ids
        : (cmd.tab_id !== undefined ? [cmd.tab_id] : []);
      if (ids.length === 0) return { ok: false, error: "no tab id provided" };
      await chrome.tabs.remove(ids);
      return { ok: true, closed: ids.length };
    }
    case "close_matching": {
      const needle = String(cmd.match || "").toLowerCase();
      if (!needle) return { ok: false, error: "match string required" };
      const all = await chrome.tabs.query({});
      const targets = all.filter(t =>
        (t.title || "").toLowerCase().includes(needle) ||
        (t.url || "").toLowerCase().includes(needle)
      );
      if (targets.length === 0) {
        return { ok: false, error: "no tab matched",
                 open_titles: all.map(t => t.title).slice(0, 20) };
      }
      await chrome.tabs.remove(targets.map(t => t.id));
      return { ok: true, closed: targets.length,
               closed_titles: targets.map(t => t.title) };
    }
    case "open": {
      if (!cmd.url) return { ok: false, error: "url required" };
      const t = await chrome.tabs.create({
        url: cmd.url, active: cmd.active !== false,
      });
      return { ok: true, tab_id: t.id, title: t.title, url: t.url };
    }
    case "activate": {
      if (cmd.tab_id === undefined) return { ok: false, error: "tab_id required" };
      const t = await chrome.tabs.update(cmd.tab_id, { active: true });
      if (t && t.windowId) {
        await chrome.windows.update(t.windowId, { focused: true });
      }
      return { ok: true, title: t?.title || "", url: t?.url || "" };
    }
    case "navigate": {
      if (cmd.tab_id === undefined || !cmd.url) {
        return { ok: false, error: "tab_id and url required" };
      }
      const t = await chrome.tabs.update(cmd.tab_id, { url: cmd.url });
      return { ok: true, title: t?.title || "", url: t?.url || "" };
    }
    case "reload": {
      await chrome.tabs.reload(cmd.tab_id, { bypassCache: !!cmd.bypass_cache });
      return { ok: true };
    }
    case "pin":
      await chrome.tabs.update(cmd.tab_id, { pinned: true });
      return { ok: true };
    case "unpin":
      await chrome.tabs.update(cmd.tab_id, { pinned: false });
      return { ok: true };
    case "mute":
      await chrome.tabs.update(cmd.tab_id, { muted: true });
      return { ok: true };
    case "unmute":
      await chrome.tabs.update(cmd.tab_id, { muted: false });
      return { ok: true };
    case "duplicate": {
      const t = await chrome.tabs.duplicate(cmd.tab_id);
      return { ok: true, new_tab_id: t.id };
    }
    case "discard":
      await chrome.tabs.discard(cmd.tab_id);
      return { ok: true };
    case "group": {
      const gid = await chrome.tabs.group({ tabIds: [cmd.tab_id] });
      if (cmd.title) await chrome.tabGroups.update(gid, { title: cmd.title });
      return { ok: true, group_id: gid };
    }
    case "ungroup":
      await chrome.tabs.ungroup(cmd.tab_id);
      return { ok: true };
    case "move": {
      const opts = {};
      if (typeof cmd.index === "number") opts.index = cmd.index;
      if (typeof cmd.window_id === "number") opts.windowId = cmd.window_id;
      await chrome.tabs.move(cmd.tab_id, opts);
      return { ok: true };
    }
    case "zoom":
      await chrome.tabs.setZoom(cmd.tab_id, cmd.factor);
      return { ok: true };
    default:
      return { error: `unknown action: ${cmd.action}` };
  }
}
'''

path = Path("data/chrome_extension/background.js")
path.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {path} ({path.stat().st_size} bytes)")

text = path.read_text(encoding="utf-8")
for needle in ("close_matching", "chrome.tabs.remove", "chrome.tabs.create", "activate"):
    assert needle in text, f"missing {needle!r}"
print("Verified: full action set present")

# py -m scripts\write_bg