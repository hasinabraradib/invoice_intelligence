// Shared helpers for the walkthrough recording: reading the script, subtitle timing and
// wrapping, and the in-page overlay (fake cursor, click ripple, highlight box, title card).
import fs from "node:fs";

export const VIEW = { width: 1920, height: 1080 };

/** Scenes and cues from docs/walkthrough/script.md (table rows that start with a cue id). */
export function readScript(file) {
  const scenes = [];
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const heading = /^## (\d+)\. (.+?) \((\d+) s\)/.exec(line);
    if (heading) {
      scenes.push({ number: Number(heading[1]), title: heading[2], budget: Number(heading[3]), cues: [] });
      continue;
    }
    const row = /^\| `([a-z0-9-]+)` \| (.+?) \| (.+?) \|$/.exec(line);
    if (row && scenes.length) scenes.at(-1).cues.push({ id: row[1], text: row[2].trim(), onScreen: row[3].trim() });
  }
  return scenes;
}

/** Reading time: about 2.5 words per second, at least 2 seconds. */
export function readingSeconds(text) {
  const words = text.split(/\s+/).filter(Boolean).length;
  return Math.max(2, words / 2.5);
}

/** One line, or two balanced lines of at most about 12 words each. */
export function wrapSubtitle(text) {
  const words = text.split(/\s+/);
  if (words.length <= 9) return [text];
  let best = 1;
  let bestDiff = Infinity;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(" ").length;
    const b = words.slice(i).join(" ").length;
    // Prefer breaking after punctuation, then the most even split.
    const bonus = /[.,:;]$/.test(words[i - 1]) ? -12 : 0;
    const diff = Math.abs(a - b) + bonus;
    if (diff < bestDiff && i <= 12 && words.length - i <= 12) {
      best = i;
      bestDiff = diff;
    }
  }
  return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
}

/** Runs in every page before its own scripts: draws the overlay layer. Pointer events
 * pass through it, so it never changes how the app behaves. */
export const OVERLAY = `
(() => {
  if (window.__wt) return;
  const Z = 2147483647;
  const css = \`
    #wt-cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: \${Z};
      pointer-events: none; transform: translate(-3px, -2px); transition: opacity .2s; }
    .wt-ripple { position: fixed; z-index: \${Z}; pointer-events: none; width: 16px; height: 16px;
      margin: -8px 0 0 -8px; border-radius: 50%; border: 3px solid rgba(23,21,18,.55);
      animation: wt-ripple .6s ease-out forwards; }
    @keyframes wt-ripple { to { transform: scale(4); opacity: 0; } }
    #wt-highlight { position: fixed; z-index: \${Z - 1}; pointer-events: none; border-radius: 14px;
      border: 3px solid #e3c254; box-shadow: 0 0 0 6px rgba(242,214,117,.28);
      transition: all .35s ease; opacity: 0; }
    #wt-ghost { position: fixed; z-index: \${Z - 2}; pointer-events: none; padding: 10px 16px;
      border-radius: 12px; background: #171512; color: #f5efe4; font: 600 18px/1.2 system-ui, sans-serif;
      box-shadow: 0 10px 30px rgba(0,0,0,.25); transform: translate(18px, 18px); }
    #wt-card { position: fixed; inset: 0; z-index: \${Z - 3}; display: grid; place-items: center;
      background: #f5efe4; color: #171512; font-family: system-ui, sans-serif; transition: opacity .35s; }
    #wt-card .n { font: 600 28px/1 system-ui, sans-serif; color: #5a5349; letter-spacing: .08em; }
    #wt-card .t { font: 700 84px/1.1 system-ui, sans-serif; letter-spacing: -0.03em; margin-top: 18px; }
    #wt-card .bar { width: 120px; height: 10px; border-radius: 5px; background: #f2d675; margin: 28px auto 0; }
  \`;
  const state = { x: -50, y: -50 };
  function ensure() {
    if (!document.body || document.getElementById("wt-cursor")) return;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    const cursor = document.createElement("div");
    cursor.id = "wt-cursor";
    cursor.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l7 19 2.8-7.6L20.5 11z" fill="#171512" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(cursor);
    const hl = document.createElement("div");
    hl.id = "wt-highlight";
    document.body.appendChild(hl);
    place();
  }
  function place() {
    const c = document.getElementById("wt-cursor");
    if (c) c.style.transform = \`translate(\${state.x - 3}px, \${state.y - 2}px)\`;
    const g = document.getElementById("wt-ghost");
    if (g) g.style.transform = \`translate(\${state.x + 18}px, \${state.y + 18}px)\`;
  }
  window.addEventListener("mousemove", (e) => { state.x = e.clientX; state.y = e.clientY; ensure(); place(); }, true);
  window.addEventListener("mousedown", (e) => {
    ensure();
    const r = document.createElement("div");
    r.className = "wt-ripple";
    r.style.left = e.clientX + "px";
    r.style.top = e.clientY + "px";
    document.body.appendChild(r);
    setTimeout(() => r.remove(), 700);
  }, true);
  document.addEventListener("DOMContentLoaded", ensure);
  window.__wt = {
    ensure,
    highlight(rect) {
      ensure();
      const hl = document.getElementById("wt-highlight");
      if (!hl) return;
      const pad = 8;
      Object.assign(hl.style, { left: rect.x - pad + "px", top: rect.y - pad + "px",
        width: rect.width + pad * 2 + "px", height: rect.height + pad * 2 + "px", opacity: "1" });
    },
    clearHighlight() {
      const hl = document.getElementById("wt-highlight");
      if (hl) hl.style.opacity = "0";
    },
    ghost(label) {
      ensure();
      let g = document.getElementById("wt-ghost");
      if (!label) { if (g) g.remove(); return; }
      if (!g) { g = document.createElement("div"); g.id = "wt-ghost"; document.body.appendChild(g); }
      g.textContent = label;
      place();
    },
    card(number, title) {
      ensure();
      let c = document.getElementById("wt-card");
      if (!c) { c = document.createElement("div"); c.id = "wt-card"; document.body.appendChild(c); }
      c.innerHTML = '<div style="text-align:center"><div class="n">SCENE ' + number + '</div><div class="t"></div><div class="bar"></div></div>';
      c.querySelector(".t").textContent = title;
      c.style.opacity = "1";
    },
    hideCard() {
      const c = document.getElementById("wt-card");
      if (c) { c.style.opacity = "0"; setTimeout(() => c.remove(), 400); }
    },
  };
})();
`;
