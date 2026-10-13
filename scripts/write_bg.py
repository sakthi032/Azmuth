
"""Write data/chrome_extension/background.js and verify."""
from pathlib import Path

CONTENT = '''// Azmuth bridge extension. Works in any Chromium browser.
// Reports its browser name on connect so the Python bridge can route
// commands to the correct browser.

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

async function execute(cmd) {
  switch (cmd.action) {
    case "list": {
      const tabs = await chrome.tabs.query({});
      return {
        tabs: tabs.map((t) => ({
          id: t.id,
          title: t.title,
          url: t.url,
          pinned: t.pinned,
          muted: t.mutedInfo?.muted || false,
          active: t.active,
          windowId: t.windowId,
          index: t.index,
        })),
      };
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
    case "group": {
      const groupId = await chrome.tabs.group({ tabIds: [cmd.tab_id] });
      if (cmd.title) await chrome.tabGroups.update(groupId, { title: cmd.title });
      return { ok: true, group_id: groupId };
    }
    case "duplicate": {
      const newTab = await chrome.tabs.duplicate(cmd.tab_id);
      return { ok: true, new_tab_id: newTab.id };
    }
    case "discard":
      await chrome.tabs.discard(cmd.tab_id);
      return { ok: true };
    default:
      return { error: `unknown action: ${cmd.action}` };
  }
}
'''

path = Path("data/chrome_extension/background.js")
path.write_text(CONTENT, encoding="utf-8")
print(f"Wrote {path} ({path.stat().st_size} bytes)")

# Verify the key lines are present.
text = path.read_text(encoding="utf-8")
for needle in ("detectBrowser", "browser: browserName", "edg/", "opr/"):
    assert needle in text, f"missing {needle!r} after write"
print("Verified: browser detection is present")

# py -m scripts\write_bg