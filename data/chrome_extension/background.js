// Azmuth bridge extension. Works in any Chromium browser.
// Reports its browser name on connect so the Python bridge can route
// commands to the correct browser. Implements the full chrome.tabs
// action set so tab control works without the CDP debug port.

const BRIDGE_URL = "ws://127.0.0.1:9280";
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
        type: "hello",
        browser: browserName,
        version: "1.0.0",
      }));
    } catch {}
  };

  socket.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (!msg || !msg.id || !msg.action) return;
    let result;
    try {
      result = await execute(msg);
    } catch (err) {
      result = { error: String(err) };
    }
    try { socket.send(JSON.stringify({ id: msg.id, result })); } catch {}
  };

  socket.onclose = () => { socket = null; };
  socket.onerror = () => { socket = null; };
}

chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RECONNECT_ALARM) connect();
});

connect();
setInterval(connect, 5000);

function serializeTab(t) {
  return {
    id: t.id,
    title: t.title,
    url: t.url,
    pinned: t.pinned,
    muted: t.mutedInfo ? t.mutedInfo.muted : false,
    active: t.active,
    windowId: t.windowId,
    index: t.index,
  };
}

async function execute(cmd) {
  switch (cmd.action) {
    case "list": {
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.map(serializeTab) };
    }
    case "eval_js": {
      if (cmd.tab_id === undefined || !cmd.javascript) {
        return { ok: false, error: "tab_id and javascript required" };
      }
      const results = await chrome.scripting.executeScript({
        target: { tabId: cmd.tab_id },
        func: (code) => {
          try {
            // eslint-disable-next-line no-new-func
            const fn = new Function("return (" + code + ")");
            return { value: fn() };
          } catch (e) {
            return { error: String(e) };
          }
        },
        args: [cmd.javascript],
        world: "MAIN",
      });
      const first = results && results[0];
      if (!first) return { ok: false, error: "no result" };
      const r = first.result;
      if (r && r.error) return { ok: false, error: r.error };
      return { ok: true, value: r ? r.value : null };
    }
    case "click_on_page": {
      if (cmd.tab_id === undefined || !cmd.text) {
        return { ok: false, error: "tab_id and text required" };
      }
      const results = await chrome.scripting.executeScript({
        target: { tabId: cmd.tab_id },
        func: (needle) => {
          const lower = needle.toLowerCase();
          const els = Array.from(document.querySelectorAll(
            'a, button, [role="button"], [role="menuitem"], ytd-rich-item-renderer, div, span'
          ));
          for (const el of els) {
            if (el.offsetParent === null) continue;
            const txt = (el.innerText || el.textContent || "").trim();
            if (!txt) continue;
            if (txt.toLowerCase().includes(lower)) {
              el.scrollIntoView({ block: "center" });
              el.click();
              return { clicked: true, tag: el.tagName, text: txt.slice(0, 80) };
            }
          }
          return { clicked: false };
        },
        args: [cmd.text],
        world: "MAIN",
      });
      const first = results && results[0];
      return { ok: true, ...((first && first.result) || {}) };
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
      const targets = all.filter((t) =>
        (t.title || "").toLowerCase().includes(needle) ||
        (t.url || "").toLowerCase().includes(needle)
      );
      if (targets.length === 0) {
        return {
          ok: false,
          error: "no tab matched",
          open_titles: all.map((t) => t.title).slice(0, 20),
        };
      }
      await chrome.tabs.remove(targets.map((t) => t.id));
      return {
        ok: true,
        closed: targets.length,
        closed_titles: targets.map((t) => t.title),
      };
    }
    case "open": {
      if (!cmd.url) return { ok: false, error: "url required" };
      const t = await chrome.tabs.create({
        url: cmd.url,
        active: cmd.active !== false,
      });
      return { ok: true, tab_id: t.id, title: t.title, url: t.url };
    }
    case "activate": {
      if (cmd.tab_id === undefined) return { ok: false, error: "tab_id required" };
      const t = await chrome.tabs.update(cmd.tab_id, { active: true });
      if (t && t.windowId) {
        await chrome.windows.update(t.windowId, { focused: true });
      }
      return { ok: true, title: (t && t.title) || "", url: (t && t.url) || "" };
    }
    case "navigate": {
      if (cmd.tab_id === undefined || !cmd.url) {
        return { ok: false, error: "tab_id and url required" };
      }
      const t = await chrome.tabs.update(cmd.tab_id, { url: cmd.url });
      return { ok: true, title: (t && t.title) || "", url: (t && t.url) || "" };
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
      return { error: "unknown action: " + cmd.action };
  }
}