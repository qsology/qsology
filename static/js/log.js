// The log page's script. Without it the form is sent the plain way, and
// that works too. With it:
//
// - A QSO is kept in this browser (localStorage) the moment Log QSO is
//   pressed, the form is cleared for the next one straight away, and the
//   QSO is sent in the background. Until the server has it, it's listed in
//   the "not sent yet" box with what's happening, can be downloaded as an
//   ADIF file, and is tried again when the connection's back. The uuid in
//   the form makes sending it twice harmless.
// - A callsign look up (htmx, into #lookup) fills in fields that are empty,
//   that the user hasn't typed in and that the cursor isn't in. It never
//   moves the cursor or the selection, and never touches the field the
//   user is in.
//
// If localStorage can't be used, the form is left to be sent the plain way.

const form = document.getElementById("qso-entry");
const user = document.body.dataset.user;
const outboxElement = document.getElementById("outbox");
const KEY = "qsology:outbox:" + user;
// Never sent with the QSO, these are the page's own
const SKIPPED = new Set(["csrfmiddlewaretoken", "action"]);
const STICKY = new Set([
  "freq", "band", "mode", "submode", "tx_pwr", "station_callsign", "operator",
  "my_gridsquare", "my_sota_ref", "my_pota_ref", "my_wwff_ref", "contest_id",
]);
const MAX_RECENT = 20;

let sending = false;
let sendAgain = false;
let retryTimer = null;
let retryDelay = 5000;

function storageWorks() {
  try {
    const probe = KEY + ":probe";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return true;
  } catch (error) {
    return false;
  }
}

function load() {
  try {
    const items = JSON.parse(window.localStorage.getItem(KEY) || "[]");
    return Array.isArray(items) ? items : [];
  } catch (error) {
    return [];
  }
}

function save(items) {
  try {
    if (items.length) {
      window.localStorage.setItem(KEY, JSON.stringify(items));
    } else {
      window.localStorage.removeItem(KEY);
    }
    return true;
  } catch (error) {
    return false;
  }
}

function update(id, changes) {
  const items = load();
  const item = items.find((each) => each.id === id);
  if (item) {
    Object.assign(item, changes);
    save(items);
  }
  render();
}

function remove(id) {
  save(load().filter((each) => each.id !== id));
  render();
}

function value(item, name) {
  const pair = item.fields.find((each) => each[0] === name);
  return pair ? pair[1] : "";
}

function announce(text) {
  const region = document.getElementById("announce");
  if (region) {
    region.textContent = text;
  }
}

function pad(number) {
  return String(number).padStart(2, "0");
}

function newUuid() {
  if (window.crypto && window.crypto.randomUUID) {
    return window.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

function csrfToken() {
  const cookie = document.cookie.split("; ").find((each) => each.startsWith("csrftoken="));
  if (cookie) {
    return decodeURIComponent(cookie.slice("csrftoken=".length));
  }
  const input = form.querySelector("input[name=csrfmiddlewaretoken]");
  return input ? input.value : "";
}

// Sending

function scheduleRetry() {
  if (retryTimer) {
    return;
  }
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    send();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 60000);
}

async function post(item) {
  const body = new URLSearchParams();
  for (const [name, fieldValue] of item.fields) {
    body.append(name, fieldValue);
  }
  body.append("csrfmiddlewaretoken", csrfToken());
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 30000);
  try {
    // Not form.action: the look up button is named action, which hides it
    return await fetch(form.getAttribute("action"), {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "HX-Request": "true", "X-CSRFToken": csrfToken() },
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timer);
  }
}

async function send() {
  // A QSO logged while others are being sent is picked up by the loop
  // that's sending them, before it stops
  if (sending) {
    sendAgain = true;
    return;
  }
  sending = true;
  try {
    do {
      sendAgain = false;
      if (!(await sendWaiting())) {
        return;
      }
    } while (sendAgain);
  } finally {
    sending = false;
  }
}

// Sends what's waiting, in order. False if it had to stop, to be tried
// again later
async function sendWaiting() {
  for (const item of load()) {
    if (item.status !== "sending" && item.status !== "waiting") {
      continue;
    }
    update(item.id, { status: "sending", message: "" });
    let response;
    try {
      response = await post(item);
    } catch (error) {
      update(item.id, {
        status: "waiting",
        message: "There's no connection. It'll be sent when there is.",
      });
      scheduleRetry();
      return false;
    }
    if (response.status === 200 || response.status === 201) {
      const html = await response.text();
      remove(item.id);
      placeRow(item, html);
      retryDelay = 5000;
      announce(value(item, "call") + " logged.");
      continue;
    }
    if (response.status === 422) {
      update(item.id, { status: "problem", problem: await response.text() });
      announce(value(item, "call") + " wasn't logged, it needs looking at.");
      continue;
    }
    if (response.status === 401 || response.status === 403) {
      update(item.id, {
        status: "signed-out",
        message: "You've been signed out. Sign in again and it'll be sent.",
      });
      return false;
    }
    update(item.id, {
      status: "waiting",
      message: "The server couldn't take it just now. It'll be tried again.",
    });
    scheduleRetry();
    return false;
  }
  return true;
}

// The recent QSOs table

function recentBody() {
  return document.getElementById(form.dataset.recent);
}

function trimRecent(body) {
  const rows = body.querySelectorAll("tr:not(.pending)");
  for (let index = MAX_RECENT; index < rows.length; index += 1) {
    // Never take away a row the user is on
    if (!rows[index].contains(document.activeElement)) {
      rows[index].remove();
    }
  }
}

function addPendingRow(item) {
  const body = recentBody();
  if (!body) {
    return;
  }
  const empty = body.querySelector("tr.empty");
  if (empty) {
    empty.remove();
  }
  const row = document.createElement("tr");
  row.className = "pending";
  row.id = "pending-" + item.id;
  const date = value(item, "date");
  const cells = [
    date, value(item, "time").slice(0, 5), value(item, "call").toUpperCase(),
    value(item, "band"), value(item, "mode").toUpperCase(),
    value(item, "rst_sent"), value(item, "rst_rcvd"), value(item, "name"),
    "Sending",
  ];
  cells.forEach((text, index) => {
    const cell = document.createElement(index === 2 ? "th" : "td");
    if (index === 2) {
      cell.scope = "row";
    }
    cell.textContent = text;
    row.appendChild(cell);
  });
  body.insertBefore(row, body.firstChild);
}

function placeRow(item, html) {
  const body = recentBody();
  const pending = document.getElementById("pending-" + item.id);
  if (!body) {
    return;
  }
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  const row = template.content.querySelector("tr");
  if (!row || document.getElementById(row.id)) {
    if (pending) {
      pending.remove();
    }
    return;
  }
  if (pending) {
    pending.replaceWith(row);
  } else {
    body.insertBefore(row, body.firstChild);
  }
  trimRecent(body);
}

// The "not sent yet" box. Each QSO keeps its own element, and only its
// text changes as it's tried again, so nothing the user is on is replaced

const STATUS_TEXT = {
  sending: "Sending…",
  waiting: "Waiting to send.",
  problem: "Not logged, it needs looking at:",
  "signed-out": "Waiting for you to sign in again.",
  "in-form": "Put back in the form above to change.",
};

function button(text, action, id) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "secondary small";
  element.textContent = text;
  element.dataset.action = action;
  element.dataset.id = id;
  return element;
}

function itemLabel(item) {
  const parts = [value(item, "call").toUpperCase(), value(item, "date"), value(item, "time").slice(0, 5)];
  return parts.filter(Boolean).join(" ");
}

function ensureBox() {
  if (outboxElement.firstChild) {
    return outboxElement.querySelector("ol");
  }
  const heading = document.createElement("h2");
  heading.id = "outbox-title";
  heading.tabIndex = -1;
  const explanation = document.createElement("p");
  explanation.textContent =
    "These are kept in this browser until the server has them. Don't clear your browser's data until they're sent, or download them to keep a copy.";
  const list = document.createElement("ol");
  const actions = document.createElement("p");
  actions.className = "item-actions";
  actions.appendChild(button("Try sending now", "send", ""));
  actions.appendChild(button("Download as ADIF", "download", ""));
  outboxElement.append(heading, explanation, list, actions);
  outboxElement.setAttribute("aria-labelledby", heading.id);
  return list;
}

function render() {
  const items = load();
  if (!items.length) {
    if (outboxElement.hidden) {
      return;
    }
    if (outboxElement.contains(document.activeElement)) {
      // The user's in the box: it stays, saying so, until they leave it
      document.getElementById("outbox-title").textContent = "Everything's been sent";
      outboxElement.addEventListener("focusout", () => window.setTimeout(render, 0), { once: true });
      return;
    }
    outboxElement.hidden = true;
    outboxElement.replaceChildren();
    outboxElement.removeAttribute("aria-labelledby");
    return;
  }
  const list = ensureBox();
  outboxElement.hidden = false;
  document.getElementById("outbox-title").textContent =
    items.length === 1 ? "1 QSO not sent yet" : items.length + " QSOs not sent yet";
  const seen = new Set();
  for (const item of items) {
    seen.add(item.id);
    let entry = document.getElementById("outbox-" + item.id);
    if (!entry) {
      entry = document.createElement("li");
      entry.id = "outbox-" + item.id;
      const label = document.createElement("strong");
      const status = document.createElement("span");
      status.className = "status";
      const problem = document.createElement("div");
      const actions = document.createElement("span");
      actions.className = "item-actions";
      entry.append(label, " ", status, problem, actions);
      list.appendChild(entry);
    }
    const [label, , status, problem, actions] = entry.childNodes;
    label.textContent = itemLabel(item);
    status.textContent = item.message || STATUS_TEXT[item.status] || "";
    if (item.status === "problem") {
      if (problem.dataset.html !== item.problem) {
        problem.dataset.html = item.problem;
        const template = document.createElement("template");
        template.innerHTML = item.problem || "";
        problem.replaceChildren(template.content);
      }
    } else if (problem.firstChild) {
      problem.replaceChildren();
      delete problem.dataset.html;
    }
    // The buttons only change with the status, which only changes in the
    // background between states that have none
    const wanted = item.status === "problem"
      ? (problem.querySelector("[data-warnings]") ? ["anyway", "edit", "remove"] : ["edit", "remove"])
      : item.status === "signed-out" ? ["sign-in", "remove"] : ["remove"];
    const current = Array.from(actions.children, (each) => each.dataset.action);
    if (current.join() !== wanted.join() && !actions.contains(document.activeElement)) {
      actions.replaceChildren(...wanted.map((action) => {
        const name = itemLabel(item);
        if (action === "anyway") return button("Log it anyway, marked to check", action, item.id);
        if (action === "edit") return button("Put it back in the form", action, item.id);
        if (action === "sign-in") return button("Sign in again", action, item.id);
        const element = button("Remove", action, item.id);
        element.setAttribute("aria-label", "Remove " + name + " without logging it");
        return element;
      }));
    }
  }
  for (const entry of Array.from(list.children)) {
    if (!seen.has(entry.id.slice("outbox-".length)) && !entry.contains(document.activeElement)) {
      entry.remove();
    }
  }
}

function formIsEmpty() {
  const call = form.elements.call;
  return !call || !call.value.trim();
}

function putBack(item) {
  if (!formIsEmpty()) {
    announce("Log or clear the QSO in the form first.");
    window.alert("Log or clear the QSO in the form first, then put this one back.");
    return;
  }
  for (const element of Array.from(form.elements)) {
    if (element.name && !SKIPPED.has(element.name) && element.type !== "submit" && element.name !== "shown") {
      const fieldValue = value(item, element.name);
      if (element.type === "checkbox") {
        element.checked = fieldValue === "on";
      } else {
        element.value = fieldValue;
      }
    }
  }
  update(item.id, { status: "in-form", message: "" });
  // Focus: the user pressed "Put it back in the form", so on to the form
  form.elements.call.focus();
}

function adif(items) {
  const names = JSON.parse(outboxElement.dataset.adif || "{}");
  const field = (name, text) => (text ? "<" + name + ":" + text.length + ">" + text + " " : "");
  let out = "Not yet sent QSOs, saved by qsology\n<ADIF_VER:5>3.1.5 <PROGRAMID:8>QSOLOGY <EOH>\n";
  for (const item of items) {
    let record = "";
    const date = value(item, "date").replace(/-/g, "");
    const time = value(item, "time").replace(/:/g, "");
    record += field("QSO_DATE", date) + field("TIME_ON", time);
    for (const [name, fieldValue] of item.fields) {
      if (names[name]) {
        record += field(names[name], fieldValue.trim());
      }
    }
    record += field("APP_QSOLOGY_UUID", item.id);
    out += record + "<EOR>\n";
  }
  return out;
}

function download() {
  const items = load();
  if (!items.length) {
    return;
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([adif(items)], { type: "text/plain" }));
  link.download = "qsology-not-sent.adi";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

function onOutboxClick(event) {
  const target = event.target.closest("button[data-action]");
  if (!target) {
    return;
  }
  const item = load().find((each) => each.id === target.dataset.id);
  switch (target.dataset.action) {
    case "send":
      retryDelay = 5000;
      for (const each of load()) {
        if (each.status === "waiting" || each.status === "signed-out") {
          update(each.id, { status: "sending", message: "" });
        }
      }
      send();
      break;
    case "download":
      download();
      break;
    case "anyway":
      if (item) {
        const fields = item.fields.filter((pair) => pair[0] !== "accept_warnings");
        fields.push(["accept_warnings", "on"]);
        update(item.id, { status: "sending", fields, problem: "" });
        send();
      }
      break;
    case "edit":
      if (item) {
        putBack(item);
      }
      break;
    case "sign-in":
      window.location.href = outboxElement.dataset.login + "?next=" + encodeURIComponent(window.location.pathname);
      break;
    case "remove":
      if (item && window.confirm("Remove " + itemLabel(item) + "? It won't be logged.")) {
        const next = target.closest("li") && target.closest("li").nextElementSibling;
        remove(item.id);
        const focusable = next && next.querySelector("button");
        // Focus: the user pressed Remove on what's now gone, so the next
        (focusable || document.getElementById("outbox-title") || form.elements.call).focus();
      }
      break;
    default:
  }
}

// Logging

function utcNow() {
  const now = new Date();
  return {
    date: now.getUTCFullYear() + "-" + pad(now.getUTCMonth() + 1) + "-" + pad(now.getUTCDate()),
    time: pad(now.getUTCHours()) + ":" + pad(now.getUTCMinutes()) + ":" + pad(now.getUTCSeconds()),
  };
}

function clearForNext() {
  for (const element of Array.from(form.elements)) {
    if (!element.name || SKIPPED.has(element.name) || element.type === "submit") {
      continue;
    }
    if (element.name === "uuid") {
      element.value = newUuid();
    } else if (element.type === "checkbox") {
      element.checked = false;
    } else if (element.type !== "hidden" && !STICKY.has(element.name)) {
      element.value = "";
    }
    element.removeAttribute("aria-invalid");
  }
  dirty.clear();
  filled.clear();
  const lookup = document.getElementById("lookup");
  if (lookup && !lookup.contains(document.activeElement)) {
    lookup.replaceChildren();
  }
}

function onSubmit(event) {
  // The look up button is sent the plain way
  if (event.submitter && event.submitter.name === "action") {
    return;
  }
  event.preventDefault();
  const data = new FormData(form);
  const when = utcNow();
  // Left empty, the date and time are when Log QSO was pressed, not when
  // the server gets it
  if (form.elements.time && !String(data.get("time") || "").trim()) {
    data.set("time", when.time);
    if (form.elements.date && !String(data.get("date") || "").trim()) {
      data.set("date", when.date);
    }
  }
  if (form.elements.date && !String(data.get("date") || "").trim()) {
    data.set("date", when.date);
  }
  const fields = [];
  data.forEach((fieldValue, name) => {
    if (!SKIPPED.has(name) && typeof fieldValue === "string") {
      fields.push([name, fieldValue]);
    }
  });
  const id = String(data.get("uuid") || newUuid());
  const items = load().filter((each) => each.id !== id);
  const item = { id, fields, status: "sending", message: "", created: Date.now() };
  items.push(item);
  if (!save(items)) {
    // Can't keep it here, so send it the plain way
    form.submit();
    return;
  }
  const hadFocus = form.contains(document.activeElement);
  clearForNext();
  addPendingRow(item);
  render();
  if (hadFocus && form.elements.call) {
    // Focus: the user pressed Log QSO, which moves on to the next QSO
    form.elements.call.focus();
  }
  announce("Logging " + value(item, "call").toUpperCase() + ".");
  send();
}

// Look ups

// Fields the user has typed in, and ones a look up filled in, which a
// later look up may change
const dirty = new Set();
const filled = new Set();

function onInput(event) {
  if (event.isTrusted && event.target.name) {
    dirty.add(event.target.name);
    filled.delete(event.target.name);
  }
}

function fill(panel) {
  const result = panel.querySelector("[data-fill]");
  const call = form.elements.call;
  if (!result || !call) {
    return;
  }
  // Only for the call that's in the form now
  if (call.value.replace(/\s/g, "").toUpperCase() !== result.dataset.call) {
    return;
  }
  let values;
  try {
    values = JSON.parse(result.dataset.fill);
  } catch (error) {
    return;
  }
  const active = document.activeElement;
  for (const name of Object.keys(values)) {
    const input = form.elements[name];
    if (!input || input === active || input.type === "hidden" || dirty.has(name)) {
      continue;
    }
    if (input.value && !filled.has(name)) {
      continue;
    }
    input.value = values[name];
    filled.add(name);
  }
}

function start() {
  if (!form || !user || !outboxElement || !window.fetch || !storageWorks()) {
    return;
  }
  form.addEventListener("submit", onSubmit);
  form.addEventListener("input", onInput);
  outboxElement.addEventListener("click", onOutboxClick);
  // htmx sends this from the element that asked (the call), saying where
  // the answer went
  document.addEventListener("htmx:after:swap", (event) => {
    const lookup = document.getElementById("lookup");
    const ctx = event.detail && event.detail.ctx;
    if (lookup && ctx && ctx.target === lookup) {
      fill(lookup);
    }
  });
  window.addEventListener("online", () => {
    retryDelay = 5000;
    send();
  });
  // Another tab changed what's waiting
  window.addEventListener("storage", (event) => {
    if (event.key === KEY) {
      render();
    }
  });
  // Anything left from before, after a reload or signing in again
  for (const item of load()) {
    if (item.status === "signed-out" || item.status === "waiting") {
      update(item.id, { status: "sending", message: "" });
    } else if (item.status === "in-form") {
      // The form it was put back into has gone
      update(item.id, { status: "problem" });
    }
  }
  render();
  send();
}

start();
