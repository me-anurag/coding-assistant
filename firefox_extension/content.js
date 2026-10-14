// Copy & Paste Unblocker - content script
//   F8 = copy selection (keeps formatting)
//   F9 = paste last F8 copy (keeps formatting in rich editors)
//   F7 = force text selection on/off (for pages that disable selecting)
(() => {
  const api = typeof browser !== "undefined" ? browser : chrome;

  // ---------- small on-page notification ----------
  function toast(msg) {
    try {
      const d = document.createElement("div");
      d.textContent = msg;
      d.style.cssText =
        "position:fixed;z-index:2147483647;right:16px;bottom:16px;padding:8px 14px;" +
        "background:#222;color:#fff;font:14px system-ui,sans-serif;border-radius:6px;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.4);pointer-events:none;";
      (document.documentElement || document.body).appendChild(d);
      setTimeout(() => d.remove(), 1800);
    } catch (e) {}
  }

  // ---------- HTML sanitising (no scripts / handlers) ----------
  function sanitize(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script,iframe,object,embed,link,meta,style,base,form").forEach((n) => n.remove());
    doc.querySelectorAll("*").forEach((el) => {
      [...el.attributes].forEach((a) => {
        const n = a.name.toLowerCase();
        if (n.startsWith("on")) el.removeAttribute(a.name);
        else if ((n === "href" || n === "src" || n === "xlink:href") &&
                 /^\s*(javascript:|data:text\/html)/i.test(a.value)) el.removeAttribute(a.name);
      });
    });
    return doc.body.innerHTML;
  }

  // ---------- copy: build HTML with computed styles inlined ----------
  const STYLE_PROPS = [
    "color", "background-color", "font-family", "font-size", "font-weight", "font-style",
    "text-decoration", "text-align", "line-height", "letter-spacing", "text-transform",
    "list-style-type"
  ];
  const BLOCK_TAGS = new Set(["P","DIV","UL","OL","LI","H1","H2","H3","H4","H5","H6","TABLE","TR","TD","TH",
    "BLOCKQUOTE","PRE","SECTION","ARTICLE","HEADER","FOOTER","TBODY","THEAD"]);

  function applyStyle(dst, src) {
    const cs = getComputedStyle(src);
    for (const p of STYLE_PROPS) {
      const v = cs.getPropertyValue(p);
      if (!v) continue;
      if (p === "background-color" && v === "rgba(0, 0, 0, 0)") continue;
      dst.style.setProperty(p, v);
    }
  }

  function rangeToHtml(range) {
    const holder = document.createElement("div");
    holder.appendChild(range.cloneContents());

    const root = range.commonAncestorContainer.nodeType === 1
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    if (!root) return holder.innerHTML;

    // Pair each cloned element with its original so computed styles can be copied
    const originals = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = walker.nextNode())) if (range.intersectsNode(n)) originals.push(n);
    const clones = holder.querySelectorAll("*");
    const mapped = originals.length === clones.length &&
                   originals.every((o, i) => o.tagName === clones[i].tagName);
    clones.forEach((c, i) => {
      if (mapped) applyStyle(c, originals[i]);
      if (c.hasAttribute("href")) c.setAttribute("href", c.href || c.getAttribute("href"));
      if (c.hasAttribute("src")) c.setAttribute("src", c.src || c.getAttribute("src"));
    });

    // Wrapper carries the inherited look (font, colour...) of the surrounding text
    const hasBlock = [...clones].some((c) => BLOCK_TAGS.has(c.tagName));
    const wrap = document.createElement(hasBlock ? "div" : "span");
    applyStyle(wrap, root);
    while (holder.firstChild) wrap.appendChild(holder.firstChild);
    return wrap.outerHTML;
  }

  function getSelectionData() {
    const el = deepActive();
    if (el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) {
      try {
        const s = el.selectionStart, e = el.selectionEnd;
        if (typeof s === "number" && e > s) return { text: el.value.slice(s, e), html: "" };
      } catch (err) {}
    }
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    let html = "";
    for (let i = 0; i < sel.rangeCount; i++) html += rangeToHtml(sel.getRangeAt(i));
    return { text: sel.toString(), html };
  }

  function deepActive() {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    return a;
  }

  async function doCopy() {
    const d = getSelectionData();
    if (!d || !d.text) { toast("Nothing selected"); return; }
    const html = d.html ? sanitize(d.html) : "";
    await api.storage.local.set({ clip: { text: d.text, html, ts: Date.now() } });
    try { await api.runtime.sendMessage({ type: "writeClipboard", html, text: d.text }); } catch (e) {}
    toast(`Copied ${d.text.length} characters${html ? " (with formatting)" : ""}`);
  }

  // ---------- paste ----------
  function insertPlain(el, text) {
    if (el.tagName === "INPUT") text = text.replace(/\s*\n\s*/g, " ");
    let ok = false;
    try { ok = document.execCommand("insertText", false, text); } catch (e) {}
    if (!ok) {
      const s = el.selectionStart, e = el.selectionEnd;
      el.setRangeText(text, s, e, "end");
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    }
  }

  function insertRich(clip) {
    let ok = false;
    if (clip.html) {
      try { ok = document.execCommand("insertHTML", false, sanitize(clip.html)); } catch (e) {}
    }
    if (!ok) { try { ok = document.execCommand("insertText", false, clip.text); } catch (e) {} }
    return ok;
  }

  async function doPaste() {
    const { clip } = await api.storage.local.get("clip");
    if (!clip || !clip.text) { toast("Nothing copied yet - select text and press F8"); return; }
    const el = deepActive();
    if (!el) { toast("Click into a text box first"); return; }
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      insertPlain(el, clip.text);
      toast("Pasted (plain text - this box has no formatting)");
    } else if (el.isContentEditable || document.designMode === "on") {
      insertRich(clip);
      toast(clip.html ? "Pasted with formatting" : "Pasted");
    } else {
      toast("Click into a text box first");
    }
  }

  // ---------- force text selection ----------
  let forced = false;
  const stopSelect = (e) => e.stopImmediatePropagation();
  function toggleForceSelect() {
    forced = !forced;
    let st = document.getElementById("__cpu_force_select");
    if (forced) {
      st = document.createElement("style");
      st.id = "__cpu_force_select";
      st.textContent = "*,*::before,*::after{-moz-user-select:text!important;user-select:text!important;}";
      (document.head || document.documentElement).appendChild(st);
      window.addEventListener("selectstart", stopSelect, true);
    } else {
      if (st) st.remove();
      window.removeEventListener("selectstart", stopSelect, true);
    }
    toast("Force text selection: " + (forced ? "ON" : "OFF"));
  }

  // ---------- hotkeys (capture phase, runs before the page's own handlers) ----------
  window.addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
    const act = e.key === "F8" ? doCopy : e.key === "F9" ? doPaste : e.key === "F7" ? toggleForceSelect : null;
    if (!act) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    act();
  }, true);
})();
