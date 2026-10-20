// Card page. All post/agent text is rendered with textContent, never innerHTML.
(function () {
  "use strict";
  var state = { data: null, open: null, label: "", busy: false, more: false, setup: null, setupOpen: null, drafts: {} };
  var DISMISS_KEY = "unhoard.setup-dismissed";
  var TINTS = ["#e9c46a", "#8fd3b5", "#9cc0f2", "#e8a7b9", "#c2aef0", "#f0b48a"];
  var SVG = "http://www.w3.org/2000/svg";
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, props) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "class") node.className = props[k];
      else if (k === "text") node.textContent = props[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), props[k]);
      else node.setAttribute(k, props[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function icon(d, box) {
    var s = document.createElementNS(SVG, "svg");
    s.setAttribute("viewBox", "0 0 " + (box || 12) + " " + (box || 12));
    s.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(SVG, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "1.6");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    s.appendChild(p);
    return s;
  }
  var ARROW_OUT = "M4.5 2.5h5v5M9.5 2.5 3 9", PLAY = "M3.5 2.2 9.5 6l-6 3.8Z", CHECK = "M3.6 6.2 5.3 7.8 8.5 4.4";
  // X's action-row glyphs, drawn on an 18-unit grid.
  var ACTS = { replies: "M3 6.5c0-2 1.6-3.5 3.5-3.5h5C13.4 3 15 4.5 15 6.5S13.4 10 11.5 10H9l-3.5 3v-3C4 10 3 8.5 3 6.5z",
    reposts: "M4 7V5.5A1.5 1.5 0 0 1 5.5 4H13l-2-2M14 11v1.5a1.5 1.5 0 0 1-1.5 1.5H5l2 2",
    likes: "M9 15s-6-3.6-6-8a3 3 0 0 1 6-1 3 3 0 0 1 6 1c0 4.4-6 8-6 8z",
    views: "M3 15V9M7 15V5M11 15V8M15 15V3",
    saves: "M5 2.5h8V16l-4-3-4 3z" };

  function ago(stamp) {
    var t = Date.parse(stamp || "");
    if (isNaN(t)) return "";
    var days = Math.floor((Date.now() - t) / 86400000);
    return days < 1 ? "today" : days + "d";
  }

  function excerpt(text, n) {
    var flat = (text || "").replace(/\s+/g, " ").trim();
    return flat.length > n ? flat.slice(0, n - 1) + "…" : flat;
  }

  function notice(message, isError, retry) {
    var box = document.getElementById("notice");
    box.hidden = !message;
    box.textContent = message || "";
    box.className = "annunciator" + (isError ? " err" : "");
    if (retry) box.appendChild(el("button", { class: "retry", type: "button", text: "Retry", onclick: retry }));
  }

  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ "X-Unhoard": "1" }, options.headers || {});
    return fetch(path, options).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) {
          var err = new Error(body.message || "request failed (" + r.status + ")");
          err.status = r.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  // Per-browser memory that the first-run checklist was hidden. Storage can be blocked, so every access is guarded.
  function dismissed(set) {
    try {
      if (set) localStorage.setItem(DISMISS_KEY, "1");
      return localStorage.getItem(DISMISS_KEY) === "1";
    } catch (e) { return !!set; }
  }

  // Select a command's text (so Ctrl+C works whatever happens next), then try the clipboard.
  function copyCommand(code, button, text, live) {
    var range = document.createRange(), sel = window.getSelection();
    range.selectNodeContents(code);
    sel.removeAllRanges();
    sel.addRange(range);
    function done(ok) {
      button.textContent = ok ? "Copied" : "Press Ctrl+C";
      live.textContent = ok ? "Copied" : "Could not copy. The command is selected: press Ctrl+C.";
      setTimeout(function () { button.textContent = "Copy"; live.textContent = ""; }, 2200);
    }
    function fallback() {
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      done(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    else fallback();
  }

  var STATUS_WORD = { ok: "Ready", info: "Note", todo: "To do", warn: "Check" };

  function setupItem(item, live) {
    var word = item.next ? "Next" : STATUS_WORD[item.status] || "";
    var status = STATUS_WORD[item.status] ? item.status : "todo";
    var li = el("li", { class: "pf-item s-" + status + (item.next ? " next" : "") + (item.id === "demo" && item.command ? " said" : "") },
      el("div", { class: "pf-top" }, el("span", { class: "pf-dot", "aria-hidden": "true" }),
        el("span", { class: "pf-k", text: item.label }), el("span", { class: "pf-s", text: word })),
      el("p", { class: "pf-detail", text: item.detail }));
    if (item.command) {
      var code = el("code", { class: "pf-code", text: item.command });
      var copy = el("button", { class: "pf-copy", type: "button", text: "Copy", "aria-label": "Copy command: " + item.label });
      copy.addEventListener("click", function () { copyCommand(code, copy, item.command, live); });
      li.appendChild(el("div", { class: "pf-cmd" }, code, copy));
    }
    return li;
  }

  // The pre-flight checklist: open by itself until a real enriched bookmark exists, unless this browser hid it.
  function renderSetup() {
    var d = state.setup, panel = document.getElementById("setup"), lamp = document.getElementById("setup-lamp");
    if (!d) { panel.hidden = true; lamp.hidden = true; return; }
    var show = state.setupOpen === null ? d.first_run && !dismissed() : state.setupOpen;
    lamp.hidden = false;
    lamp.className = "readout setup-lamp " + (d.ready === d.total ? "live" : "held");
    lamp.setAttribute("aria-expanded", String(show));
    document.getElementById("setup-count").textContent = d.ready + "/" + d.total;
    panel.hidden = !show;
    if (!show) return;
    document.getElementById("setup-ready").textContent = d.ready + " of " + d.total + " ready";
    var list = document.getElementById("setup-list"), live = document.getElementById("copy-live");
    list.textContent = "";
    d.items.forEach(function (item) { list.appendChild(setupItem(item, live)); });
  }

  function loadSetup() {
    return api("api/setup").then(function (d) { state.setup = d; }, function () { state.setup = null; }).then(renderSetup);
  }

  function load(more) {
    if (more) state.more = true; // keep showing bonus cards for this visit
    return api("api/cards" + (state.more ? "?more=1" : "")).then(function (d) {
      state.data = d;
      var ids = d.picks.concat(d.waiting).map(function (c) { return c.action_id; });
      if (ids.indexOf(state.open) < 0) state.open = null;
      render();
      loadSetup();
    }).catch(function (e) {
      notice("Could not load cards (" + e.message + "). Is unhoard serve still running?", true,
        function () { notice(""); load(); });
    });
  }

  // What the first lamp says. The server decides, from the configured adapter and whether Omnigent is there:
  // "Go" when it starts something (an Omnigent session or your command), "Show brief" when it only prints the brief.
  function launchInfo() { return (state.data && state.data.launch) || {}; }
  function goLabel() { return launchInfo().go_label === "Show brief" ? "Show brief" : "Go"; }

  function decide(card, decision, note, lamps, panel) {
    if (state.busy) return;
    state.busy = true;
    lamps.forEach(function (b) { b.disabled = true; if (b.dataset.decision === decision) b.classList.add("lit"); });
    notice(decision === "go" ? "Working on: " + card.title + "…" : "Saving…", false);
    var request = { decision: decision, note: note };
    // Tell the server which target the button promised. If Omnigent came or went since this page loaded, the
    // server refuses (409) instead of doing something the button did not say.
    if (decision === "go" && launchInfo().target) request.expect_target = launchInfo().target;
    api("api/cards/" + card.action_id + "/decide", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request)
    }).then(function (r) {
      delete state.drafts[card.action_id];  // answered: the note went with the answer (or was not wanted)
      notice(decision === "go" ? goneNotice(r, card) :
             decision === "later" ? "Held for a few days: " + card.title : "Skipped: " + card.title, false);
      if (decision === "go" && r.launch_target === "print" && typeof r.brief === "string") showBrief(card, r.brief);
      panel.classList.add("leaving");
      return new Promise(function (ok) { setTimeout(ok, reduced ? 0 : 230); });
    }).then(function () {
      state.busy = false;
      state.open = null;
      state.arrived = decision === "go" ? card.action_id : null;
      return load();
    }).catch(function (e) {
      state.busy = false;
      lamps.forEach(function (b) { b.disabled = false; b.classList.remove("lit"); });
      if (e.status === 409 && e.body && e.body.reason === "target_changed") {
        // nothing was started and the card is still here: reload so the button says what it now does
        load().then(function () {
          notice("Omnigent came or went, so the button changed and nothing was started. Check the button and press it again.", true);
        });
        return;
      }
      notice(e.message, true);
    });
  }

  // The notice after Go comes from what the server says it did, not from the label the page showed.
  function goneNotice(r, card) {
    if (r.launch_target === "print") return "Brief ready, shown above: " + card.title;
    if (r.launch_target === "omnigent") return "Started in Omnigent: " + card.title;
    if (r.launch_target === "command") return "Started your command: " + card.title;
    return "Launched: " + card.title;
  }

  // "Show brief": the brief text, as plain text (textContent only), above the cards, with a Copy button.
  function showBrief(card, text) {
    var sect = document.getElementById("brief-sect"), pre = document.getElementById("brief-text");
    pre.textContent = text;
    document.getElementById("brief-for").textContent = card.title;
    sect.hidden = false;
  }
  document.getElementById("brief-close").addEventListener("click", function () {
    document.getElementById("brief-sect").hidden = true;
  });
  document.getElementById("brief-copy").addEventListener("click", function () {
    var button = this, pre = document.getElementById("brief-text");
    copyCommand(pre, button, pre.textContent, document.getElementById("copy-live"));
  });

  // Omnigent can start or stop while the tab is in the background. When the tab comes back, look again; redraw only
  // if what Go does or the set of cards changed, so a half-typed note is not thrown away.
  function refreshOnReturn() {
    if (document.visibilityState !== "visible" || state.busy || !state.data) return;
    api("api/cards" + (state.more ? "?more=1" : "")).then(function (d) {
      function key(x) {
        return JSON.stringify([x.launch, x.picks.concat(x.waiting).map(function (c) { return c.action_id; })]);
      }
      if (key(d) !== key(state.data)) {
        state.data = d;
        render();
        loadSetup();
      }
    }).catch(function () { /* the next click or reload reports a stopped server */ });
  }
  document.addEventListener("visibilitychange", refreshOnReturn);

  function tint(card) {
    var name = card.author_name || card.author_username || "?";
    return TINTS[(name.charCodeAt(0) + name.length) % TINTS.length];
  }

  function https(url) { return typeof url === "string" && url.indexOf("https://") === 0 ? url : null; }
  // Pictures may also be inline data: images (the demo uses them); links and video never are.
  function picture(url) { return https(url) || (typeof url === "string" && url.indexOf("data:image/") === 0 ? url : null); }

  // Author picture with an initial as the fallback (no picture, or it fails to load).
  function avatar(card, cls) {
    var name = card.author_name || card.author_username || "?";
    var tile = el("span", { class: cls || "av", "aria-hidden": "true", text: name.charAt(0).toUpperCase() });
    tile.style.background = tint(card);
    var src = picture(card.author_avatar_url);
    if (!src) return tile;
    var img = el("img", { class: (cls || "av") + " pic", src: src, alt: "", referrerpolicy: "no-referrer", loading: "lazy" });
    img.addEventListener("error", function () { if (img.parentNode) img.parentNode.replaceChild(tile, img); });
    return img;
  }

  function who(card, short) {
    var name = card.author_name || card.author_username || "unknown";
    var when = short ? "" : ago(card.tweet_created_at || card.first_seen_at);
    return el("span", { class: "who" }, el("b", { text: name }),
      el("span", { text: (card.author_username ? "@" + card.author_username : "") + (when ? " · " + when + (when === "today" ? "" : " ago") : "") }));
  }

  function priority(card) {
    return el("span", { class: "pri p-" + card.priority, text: card.priority });
  }

  function mediaList(card) {
    if (card.media && card.media.length) return card.media;
    return card.media_url ? [{ type: "photo", url: card.media_url }] : [];
  }

  function firstImage(card) {
    var m = mediaList(card)[0];
    return m ? picture(m.url) : null;
  }

  function duration(ms) {
    if (!ms) return "";
    var s = Math.round(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  // Photos, videos and GIFs in attachment order, tiled like the post itself.
  function mediaGrid(card) {
    var items = mediaList(card).slice(0, 4);
    if (!items.length) return null;
    var grid = el("div", { class: "mgrid n" + items.length });
    items.forEach(function (m) {
      var poster = picture(m.url), video = https(m.video_url), node;
      if (video && m.type === "animated_gif") {
        node = el("video", { class: "mcell", src: video, muted: "", loop: "", playsinline: "", preload: "metadata", "aria-label": m.alt_text || "GIF" });
        if (poster) node.setAttribute("poster", poster);
        node.muted = true;
        if (!reduced) node.autoplay = true;
      } else if (video) {
        // No src until Play, so nothing is fetched from the video host unless the user asks.
        node = el("video", { class: "mcell", playsinline: "", preload: "none", "aria-label": m.alt_text || "Video" });
        if (poster) node.setAttribute("poster", poster);
      } else if (poster) {
        node = el("img", { class: "mcell", src: poster, alt: m.alt_text || "", referrerpolicy: "no-referrer", loading: "lazy" });
      }
      if (!node) return;
      var cell = el("div", { class: "mwrap" }, node);
      // At rest a video shows its poster and our play button; the browser's own controls appear once it plays.
      if (video && m.type !== "animated_gif") {
        var play = el("button", { class: "mplay", type: "button", "aria-label": "Play video" });
        play.appendChild(icon(PLAY));
        play.addEventListener("click", function (e) {
          e.stopPropagation();
          node.src = video;
          node.controls = true;
          play.remove();
          node.play().catch(function () {});
        });
        cell.appendChild(play);
      }
      if (m.type === "video" && m.duration_ms) cell.appendChild(el("span", { class: "mtime", text: duration(m.duration_ms) }));
      if (m.type === "animated_gif") cell.appendChild(el("span", { class: "mtime", text: "GIF" }));
      grid.appendChild(cell);
    });
    return grid.childNodes.length ? grid : null;
  }

  // Post text with its own line breaks; @mentions, #tags and links set apart.
  function postText(card, cls) {
    var p = el("p", { class: cls });
    var text = card.text_display || card.text || "";
    var links = card.links || [];
    text.split(/(@\w+|#\w+|\b(?:https?:\/\/)?[\w-]+(?:\.[\w-]+)+\/\S*)/).forEach(function (part) {
      if (!part) return;
      if (/^[@#]\w+$/.test(part)) {
        p.appendChild(el("span", { class: "m", text: part }));
        return;
      }
      var hit = links.filter(function (l) { return l.display === part; })[0];
      if (hit && https(hit.url)) p.appendChild(el("a", { class: "m", href: hit.url, target: "_blank", rel: "noopener noreferrer", text: part }));
      else p.appendChild(document.createTextNode(part));
    });
    return p;
  }

  function count(n) {
    if (n == null) return null;
    return n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : n >= 1e3 ? (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K" : String(n);
  }

  // The post as it looks on X: picture, name and handle, the words, the media, then the action row.
  function xpost(card, full) {
    var box = el("div", { class: "xpost" + (full ? " full" : "") });
    var name = card.author_name || card.author_username || "unknown";
    var when = ago(card.tweet_created_at || card.first_seen_at);
    var line = el("div", { class: "xname" }, el("b", { text: name }));
    if (card.author_verified) {
      var badge = el("span", { class: "xbadge", "aria-label": "Verified" });
      badge.appendChild(icon(CHECK));
      line.appendChild(badge);
    }
    line.appendChild(el("span", { text: (card.author_username ? "@" + card.author_username : "") + (when ? " · " + when : "") }));
    var src = https(card.source_url);
    if (src) {
      var a = el("a", { class: "xopen", href: src, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open on X" });
      a.appendChild(icon(ARROW_OUT));
      line.appendChild(a);
    }
    var body = el("div", { class: "xbody" }, line);
    var text = postText(card, "xtext");
    body.appendChild(text);
    if (!full) {
      var more = el("button", { class: "xmore", type: "button", text: "Show more" });
      more.addEventListener("click", function () { box.classList.add("expanded"); more.remove(); });
      body.appendChild(more);
      requestAnimationFrame(function () { if (text.scrollHeight <= text.clientHeight + 2) more.remove(); });
    }
    var grid = mediaGrid(card);
    if (grid) body.appendChild(grid);
    var m = card.metrics || {};
    var acts = ["replies", "reposts", "likes", "views", "saves"].filter(function (k) { return m[k] != null; });
    if (acts.length) {
      var row = el("div", { class: "xacts" });
      acts.forEach(function (k) {
        var s = el("span", { "aria-label": count(m[k]) + " " + k });
        s.appendChild(icon(ACTS[k], 18));
        s.appendChild(document.createTextNode(count(m[k])));
        row.appendChild(s);
      });
      body.appendChild(row);
    }
    box.appendChild(avatar(card, "xav"));
    box.appendChild(body);
    return box;
  }

  function field(key, node) {
    return el("div", { class: "field" }, el("span", { class: "field-k", text: key }), node);
  }

  // Every URL the server found in the text the agent wrote (title, brief, why_saved; at most 10, and "+N more" says
  // how many were left out). The text came from a model that read an untrusted post, so they are shown before Go.
  // Each URL is printed in full, scheme as written, as plain text; only an https:// one is a link. Invisible and
  // direction-changing characters (U+202E and friends) are shown as <U+XXXX> markers, and such a URL is not a link.
  // (the u flag lets the class name astral characters: the Unicode tag block U+E0000-E007F is invisible text)
  var HIDDEN = /[\u0000-\u001f\u007f-\u009f\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e\u200b-\u200f\u2028-\u202f\u205f-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff9-\ufffb\u{E0000}-\u{E007F}]/gu;
  function taskUrls(card) {
    return (card.relate_urls || []).filter(function (u) { return typeof u === "string" && u; });
  }

  function linksField(card, urls) {
    if (!urls.length) return null;
    var list = el("ul", { class: "urls" });
    urls.forEach(function (u) {
      var marked = u.replace(HIDDEN, function (c) { return "<U+" + c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0") + ">"; });
      var safe = marked === u ? https(u) : null;
      list.appendChild(el("li", {}, safe ? el("a", { href: safe, target: "_blank", rel: "noopener noreferrer", text: marked })
        : el("span", { text: marked })));
    });
    if (card.relate_urls_more > 0) list.appendChild(el("li", { text: "+" + card.relate_urls_more + " more not shown" }));
    return field("Links in this task", list);
  }

  function lampRow(card, noteBox, panel) {
    var lamps = [["go", goLabel()], ["later", "Later"], ["skip", "Skip"]].map(function (d) {
      return el("button", { class: "lamp " + d[0], type: "button", "data-decision": d[0], text: d[1],
        "aria-label": d[1] + ": " + card.title });
    });
    lamps.forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        decide(card, b.dataset.decision, noteBox ? noteBox.value : "", lamps, panel);
      });
    });
    return el("div", { class: "lamps" }, lamps[0], lamps[1], lamps[2]);
  }

  // A station: one card with everything needed to answer it. Picks are stations; a waiting row opens into one.
  function station(card, full) {
    var panel = el("article", { class: "station" + (full ? " full" : ""), "aria-label": card.title });
    var head = el("div", { class: "station-head" }, priority(card), card.label ? el("span", { class: "tape", text: card.label }) : null,
      el("span", { class: "saved", text: "saved " + ago(card.first_seen_at) + (ago(card.first_seen_at) === "today" ? "" : " ago") }));
    var urls = taskUrls(card);
    var note = el("textarea", { placeholder: "Note for the agent, e.g. “focus on the free tier”", "aria-label": "Note for the agent" });
    // A typed note outlives every redraw (a 409 reload, opening another row) until the card is answered.
    note.value = state.drafts[card.action_id] || "";
    note.addEventListener("input", function () { state.drafts[card.action_id] = note.value; });
    var briefNodes = [field("Brief", el("p", { class: "brief", text: card.brief || "" })), note];
    var taskCol = [field("Why you saved it", el("p", { class: "why", text: card.why_saved || "unknown" })),
      field("Task", el("p", { class: "task", text: card.title })), linksField(card, urls),
      card.launch_error ? el("p", { class: "fault", text: "Last launch failed: " + card.launch_error }) : null];
    var body;
    if (full) {
      body = el("div", { class: "station-body" }, el("div", { class: "col" }, xpost(card, true)),
        el("div", { class: "col" }, taskCol[0], taskCol[1], taskCol[2], taskCol[3], briefNodes[0], briefNodes[1], lampRow(card, note, panel)));
    } else {
      body = el("div", { class: "station-body" }, xpost(card, false), taskCol[0], taskCol[1], taskCol[2], taskCol[3],
        el("div", { class: "spacer" }), briefNodes[0], briefNodes[1]);
    }
    panel.appendChild(head);
    panel.appendChild(body);
    if (!full) panel.appendChild(lampRow(card, note, panel));
    return panel;
  }

  function row(card) {
    var pic = firstImage(card);
    var thumb = pic ? el("img", { class: "thumb", src: pic, alt: "", referrerpolicy: "no-referrer", loading: "lazy" })
      : el("span", { class: "thumb none", "aria-hidden": "true" });
    var r = el("div", { class: "row", role: "listitem", tabindex: "0", "aria-label": card.title,
      onclick: function () { state.open = card.action_id; render(); },
      onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); state.open = card.action_id; render(); } } },
      el("span", { class: "dot p-" + card.priority, title: card.priority + " priority" }), thumb,
      el("span", { class: "row-who" }, avatar(card, "row-av"), who(card, true)),
      el("span", { class: "excerpt", text: excerpt(card.text_display || card.text, 120) }),
      el("span", { class: "rtask", text: card.title }),
      el("span", { class: "tape-cell" }, card.label ? el("span", { class: "tape", text: card.label }) : null),
      el("span", { class: "age", text: ago(card.tweet_created_at || card.first_seen_at) }));
    return r;
  }

  function launchedRow(card) {
    var failed = card.launch_status === "failed";
    return el("div", { class: "row" + (card.action_id === state.arrived ? " arrived" : ""), role: "listitem" },
      el("span", { class: "dot " + (failed ? "p-high" : "ok") }),
      el("span", { class: "rtask", text: card.title }), who(card, true),
      card.launch_ref && /^https?:/.test(card.launch_ref)
        ? el("a", { href: card.launch_ref, target: "_blank", rel: "noopener noreferrer", class: "state", text: "Open session" })
        : el("span", { class: "state" + (failed ? " failed" : ""), text: failed ? "Failed" : (card.launch_status || "Launched") }));
  }

  function readouts(d) {
    var box = document.getElementById("readouts");
    box.textContent = "";
    var items = [["Today", d.picks.length, "live"], ["Waiting", d.waiting.length, ""], ["Later", d.later_count || 0, "held"]];
    if (d.launched) items.push(["Launched", d.launched.length, "live"]);
    items.forEach(function (it) {
      box.appendChild(el("div", { class: "readout " + it[2] }, el("span", { class: "dot", "aria-hidden": "true" }),
        el("dt", { text: it[0] }), el("dd", { text: String(it[1]).padStart(2, "0") })));
    });
  }

  function empty(text, extra) {
    var box = el("div", { class: "empty" }, el("span", { text: text }));
    if (extra) box.appendChild(extra);
    return box;
  }

  function render() {
    var d = state.data;
    if (!d) return;
    readouts(d);
    var fl = document.getElementById("filters");
    fl.textContent = "";
    fl.hidden = !d.labels.length;
    [""].concat(d.labels).forEach(function (l) {
      fl.appendChild(el("button", { type: "button", "aria-pressed": String(l === state.label), text: l || "All",
        onclick: function () { state.label = l; render(); } }));
    });
    var match = function (c) { return !state.label || c.label === state.label; };
    var picks = d.picks.filter(match), waiting = d.waiting.filter(match), launched = (d.launched || []).filter(match);
    var pbox = document.getElementById("picks"), wbox = document.getElementById("waiting"), lbox = document.getElementById("launched");
    pbox.textContent = ""; wbox.textContent = ""; lbox.textContent = "";
    picks.forEach(function (c) { pbox.appendChild(station(c, false)); });
    waiting.forEach(function (c) {
      wbox.appendChild(c.action_id === state.open ? el("div", { class: "row-open", role: "listitem" }, station(c, true)) : row(c));
    });
    launched.forEach(function (c) { lbox.appendChild(launchedRow(c)); });
    if (!picks.length) {
      if (d.waiting.length || d.later_count) {
        var more = el("button", { class: "plain", type: "button", text: "Poll " + d.daily_cards + " more", onclick: function () { load(true); } });
        pbox.appendChild(empty("That was today’s " + d.daily_cards + ".", more));
      } else {
        pbox.appendChild(empty("No cards yet. Run", el("code", { text: "unhoard sync && unhoard enrich" })));
      }
    }
    document.getElementById("waiting-sect").hidden = !waiting.length;
    document.getElementById("launched-sect").hidden = !launched.length;
    document.getElementById("foot").textContent = d.later_count ? d.later_count + " held until their Later time is up." : "";
    if (state.open) {
      var opened = wbox.querySelector(".row-open");
      if (opened && opened.scrollIntoView) opened.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
    }
  }

  document.getElementById("setup-lamp").addEventListener("click", function () {
    var open = !document.getElementById("setup").hidden;
    state.setupOpen = !open;
    renderSetup();
  });
  document.getElementById("setup-hide").addEventListener("click", function () {
    if (state.setup && state.setup.first_run) dismissed(true);
    state.setupOpen = false;
    renderSetup();
    document.getElementById("setup-lamp").focus();
  });

  var pbox0 = document.getElementById("picks");
  for (var i = 0; i < 3; i++) pbox0.appendChild(el("div", { class: "skeleton", "aria-hidden": "true" }));
  load();
})();
