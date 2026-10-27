// Every page's script. Loaded as a module, so only browsers new enough for
// modules run it; older ones get the pages as plain HTML, which do
// everything too. Nothing here is needed for anything to work, see
// docs/web.md.

const root = document.documentElement;
root.classList.add("js");

// Content htmx swaps in must never take the focus from where the user is.
// A swap that would replace the element they're on is dropped, unless they
// started it from inside that element (pressing a button that's replaced
// by its result, say), when htmx puts the focus back on the element with
// the same id
const INSERTS = new Set(["beforebegin", "afterbegin", "beforeend", "afterend", "none"]);

document.addEventListener("htmx:before:swap", (event) => {
  const active = document.activeElement;
  if (!active || active === document.body || active === root) {
    return;
  }
  const detail = event.detail || {};
  const source = detail.ctx && detail.ctx.sourceElement;
  for (const task of detail.tasks || []) {
    const style = task.swapSpec && task.swapSpec.style;
    const target = task.target;
    if (INSERTS.has(style) || !target || !target.contains) {
      continue;
    }
    if (target.contains(active) && !(source && target.contains(source))) {
      event.preventDefault();
      return;
    }
  }
});

// htmx needs fetch and modern JavaScript. Where it can't load, the hx-
// attributes do nothing and the plain links and forms are used
if (window.fetch && window.AbortController && root.dataset.htmx) {
  import(root.dataset.htmx).catch(() => {
    root.classList.add("no-htmx");
  });
}
