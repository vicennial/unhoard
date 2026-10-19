// Hand-written, dependency-free bundle for the Omnigent V1 browser-extension sandbox.
// It renders static text only: the sandbox has no network and no session-create API.
// (The official SDK is a private workspace package, so this scaffold does not use it.)
(function () {
  "use strict";
  var root = document.getElementById("root");
  if (!root) {
    root = document.createElement("div");
    root.id = "root";
    document.body.appendChild(root);
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }
  var page = el("main", "bk-page");
  var card = el("section", "bk-card");
  card.appendChild(el("span", "bk-eyebrow", "Omnigent extension · v0.1 scaffold"));
  card.appendChild(el("h1", "", "Bookmarks"));
  card.appendChild(el("p", "", "Your saved posts, turned into one concrete task each. This tab cannot show them yet."));
  card.appendChild(el("p", "",
    "Extension pages run in a sandbox with no network access and no way to start sessions. " +
    "This tab needs an upcoming Omnigent extension API: sessions.create plus an approved local data source."));
  card.appendChild(el("p", "bk-label", "Until then, run this in a terminal:"));
  card.appendChild(el("pre", "bk-code", "unhoard serve"));
  card.appendChild(el("p", "bk-muted", "Then open http://127.0.0.1:8765 in your browser. Cards, notes and Go / Later / Skip work there today."));
  page.appendChild(card);
  root.appendChild(page);
})();
