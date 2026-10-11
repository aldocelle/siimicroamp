/* SII Hub frontend: sin frameworks, sin servicios externos. */
const $ = (selector) => document.querySelector(selector);
let csrf = "";
let registerMode = false;
let selectedDocument = null;
let searchTimer;

const clp = (value) => new Intl.NumberFormat("es-CL", {
  style: "currency", currency: "CLP", maximumFractionDigits: 0
}).format(value ?? 0);

const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) {
    headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  let body = {};
  try { body = await response.json(); } catch { /* error HTTP sin JSON */ }
  if (!response.ok) {
    const message = typeof body.detail === "string" ? body.detail : "No se pudo completar la operación.";
    throw new Error(message);
  }
  return body;
}

function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = error ? "visible error" : "visible";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = ""; }, 6000);
}

function authView() {
  $("#auth").hidden = false;
  $("#shell").hidden = true;
  $("#overlay").hidden = true;
}

function appView(user) {
  csrf = user.csrf;
  $("#auth").hidden = true;
  $("#shell").hidden = false;
  $("#org-name").textContent = user.organization;
  $("#user-email").textContent = user.email;
  loadDocuments();
}

function setRegisterMode(value) {
  registerMode = value;
  $("#register-fields").hidden = !value;
  $("#org").required = value;
  $("#auth-title").textContent = value ? "Crea tu espacio" : "Ingresa a tu espacio";
  $("#auth-description").textContent = value
    ? "Un espacio privado para organizar tus documentos."
    : "Tus documentos, organizados en un solo lugar.";
  $("#auth-submit").innerHTML = value ? "Crear cuenta <span>↗</span>" : "Ingresar <span>↗</span>";
  $("#auth-toggle").textContent = value ? "¿Ya tienes cuenta? Ingresa" : "¿No tienes cuenta? Crea una";
  $("#password").autocomplete = value ? "new-password" : "current-password";
}

async function loadDocuments() {
  const params = new URLSearchParams();
  if ($("#search").value.trim()) params.set("q", $("#search").value.trim());
  if ($("#direction").value) params.set("direction", $("#direction").value);
  try {
    const data = await api("/api/documents?" + params.toString());
    $("#metric-count").textContent = data.summary.count ?? 0;
    $("#metric-purchases").textContent = clp(data.summary.compras);
    $("#metric-sales").textContent = clp(data.summary.ventas);
    $("#metric-pending").textContent = data.summary.pendientes ?? 0;
    $("#empty").hidden = data.documents.length > 0;
    $("#documents-body").innerHTML = data.documents.map((doc) => `
      <tr>
        <td><strong>DTE ${safe(doc.tipo_dte)} · Nº ${safe(doc.folio)}</strong><small>#${safe(doc.id)}</small></td>
        <td><strong>${safe(doc.razon_emisor || "Sin razón social")}</strong><small>${safe(doc.rut_emisor)}</small></td>
        <td>${safe(doc.fecha_emision || "Sin fecha")}</td>
        <td><span class="direction-tag">${safe(({ compra: "Compra", venta: "Venta", sin_clasificar: "Sin clasificar" })[doc.direccion] || doc.direccion)}</span></td>
        <td><strong>${clp(doc.monto_total)}</strong></td>
        <td><span class="tag ${doc.estado === "revisado" ? "reviewed" : "extracted"}">${doc.estado === "revisado" ? "REVISADO" : "POR REVISAR"}</span></td>
        <td><button class="doc-link" type="button" data-doc-id="${Number(doc.id)}">Revisar ↗</button></td>
      </tr>
    `).join("");
  } catch (error) { toast(error.message, true); }
}

async function importFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".xml")) return toast("Selecciona un archivo XML DTE.", true);
  if (file.size > 10 * 1024 * 1024) return toast("El máximo permitido es 10 MB.", true);
  const form = new FormData();
  form.append("file", file);
  toast("Procesando XML…");
  try {
    const result = await api("/api/documents/import", { method: "POST", body: form });
    let message = `${result.imported} documento(s) importado(s).`;
    if (result.duplicates.length) message += ` ${result.duplicates.length} duplicado(s) omitido(s).`;
    toast(message);
    await loadDocuments();
  } catch (error) { toast(error.message, true); }
}

async function openDocument(id) {
  try {
    const doc = await api("/api/documents/" + id);
    selectedDocument = doc.id;
    $("#drawer-title").textContent = `DTE ${doc.tipo_dte} · Nº ${doc.folio}`;
    for (const [key, value] of Object.entries(doc)) {
      const input = document.getElementById("field-" + key);
      if (input) input.value = value ?? "";
    }
    $("#download-original").href = `/api/documents/${doc.id}/original`;
    const warnings = $("#warnings");
    warnings.hidden = !doc.warnings.length;
    warnings.innerHTML = doc.warnings.map((warning) => `<div>⚠ ${safe(warning)}</div>`).join("");
    $("#overlay").hidden = false;
  } catch (error) { toast(error.message, true); }
}

async function saveReview() {
  if (!selectedDocument || !$("#review-form").reportValidity()) return;
  const fields = [
    "tipo_dte", "folio", "fecha_emision", "rut_emisor", "razon_emisor",
    "rut_receptor", "razon_receptor", "monto_neto", "monto_exento",
    "iva", "monto_total", "direccion"
  ];
  const data = {};
  for (const field of fields) {
    const value = document.getElementById("field-" + field).value.trim();
    data[field] = ["monto_neto", "monto_exento", "iva", "monto_total"].includes(field)
      ? (value === "" ? null : Number(value)) : value;
  }
  const button = $("#save-review");
  button.disabled = true;
  try {
    await api("/api/documents/" + selectedDocument, { method: "PATCH", body: JSON.stringify(data) });
    $("#overlay").hidden = true;
    selectedDocument = null;
    toast("Revisión guardada. Estado interno actualizado.");
    await loadDocuments();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#auth-toggle").addEventListener("click", () => setRegisterMode(!registerMode));
  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#auth-submit");
    button.disabled = true;
    const body = {
      email: $("#email").value.trim(), password: $("#password").value
    };
    if (registerMode) {
      body.organization = $("#org").value.trim();
      body.rut = $("#org-rut").value.trim();
    }
    try {
      const result = await api(registerMode ? "/api/register" : "/api/login", {
        method: "POST", body: JSON.stringify(body)
      });
      csrf = result.csrf;
      appView(await api("/api/me"));
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });

  $("#logout").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST" });
      csrf = "";
      authView();
      toast("Sesión cerrada.");
    } catch (error) { toast(error.message, true); }
  });

  for (const id of ["xml-file", "xml-file-empty"]) {
    document.getElementById(id).addEventListener("change", async (event) => {
      await importFile(event.target.files[0]);
      event.target.value = "";
    });
  }

  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadDocuments, 300);
  });
  $("#direction").addEventListener("change", loadDocuments);
  $("#documents-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-doc-id]");
    if (button) openDocument(Number(button.dataset.docId));
  });
  $("#drawer-close").addEventListener("click", () => { $("#overlay").hidden = true; });
  $("#overlay").addEventListener("click", (event) => {
    if (event.target === $("#overlay")) $("#overlay").hidden = true;
  });
  $("#save-review").addEventListener("click", saveReview);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") $("#overlay").hidden = true;
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-view]").forEach((item) => item.classList.toggle("active", item === button));
      const view = button.dataset.view;
      $("#documents-view").hidden = view !== "documents";
      $("#connections-view").hidden = view !== "connections";
      $("#breadcrumb-current").textContent = view === "documents" ? "DOCUMENTOS" : "CONEXIONES SII";
    });
  });

  try { appView(await api("/api/me")); }
  catch { authView(); }
});
