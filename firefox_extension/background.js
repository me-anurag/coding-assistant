// Writes HTML + plain text to the real system clipboard so you can also
// use normal Ctrl+V in apps that allow it (Word, Gmail, Notepad...).
function writeClipboard(html, text) {
  const ta = document.createElement("textarea");
  ta.value = text || " ";
  document.body.appendChild(ta);
  ta.select();
  const handler = (e) => {
    e.preventDefault();
    if (html) e.clipboardData.setData("text/html", html);
    e.clipboardData.setData("text/plain", text || "");
  };
  document.addEventListener("copy", handler, { once: true });
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (err) {}
  document.removeEventListener("copy", handler);
  ta.remove();
  return ok;
}

browser.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "writeClipboard") {
    return Promise.resolve(writeClipboard(msg.html, msg.text));
  }
});
