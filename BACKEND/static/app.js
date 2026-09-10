const API = "";
let state = { token: null, role: null, nombre: null };

// ---------------------------------------------------------------------------
// Sesión
// ---------------------------------------------------------------------------

function saveSession() {
  localStorage.setItem("sst_session", JSON.stringify(state));
}

function loadSession() {
  const raw = localStorage.getItem("sst_session");
  if (raw) state = JSON.parse(raw);
}

function clearSession() {
  state = { token: null, role: null, nombre: null };
  localStorage.removeItem("sst_session");
}

async function api(path, opts = {}) {
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    opts.headers || {},
    state.token ? { Authorization: "Bearer " + state.token } : {}
  );
  const res = await fetch(API + path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    clearSession();
    renderLogin("Tu sesión expiró. Inicia sesión de nuevo.");
    throw new Error("unauthorized");
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Error de servidor");
  return data;
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

function fillCreds(username, password) {
  document.getElementById("username").value = username;
  document.getElementById("password").value = password;
}

async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";
  try {
    const res = await fetch(API + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      errorEl.textContent = data.error || "Error al iniciar sesión";
      return;
    }
    state = { token: data.token, role: data.role, nombre: data.nombre };
    saveSession();
    renderApp();
  } catch (err) {
    errorEl.textContent = "No se pudo conectar con el servidor.";
  }
}

function renderLogin(message) {
  document.getElementById("app-shell").classList.remove("visible");
  document.getElementById("login-screen").style.display = "flex";
  if (message) document.getElementById("login-error").textContent = message;
}

// ---------------------------------------------------------------------------
// Render principal
// ---------------------------------------------------------------------------

async function renderApp() {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app-shell").classList.add("visible");
  document.getElementById("user-nombre").textContent = state.nombre;
  document.getElementById("role-badge").textContent =
    state.role === "MEDICO" ? "MÉDICO OCUPACIONAL / ADMIN SST" : "HRBP / LÍDER DE ÁREA";

  try {
    const dashboard = await api("/api/dashboard/resumen");
    renderKPIs(dashboard);
    renderAreas(dashboard);
    await renderCasos();
    await renderAlertas();
    if (state.role === "MEDICO") {
      document.getElementById("admin-panel").style.display = "block";
      await renderEtlLog();
    } else {
      document.getElementById("admin-panel").style.display = "none";
    }
  } catch (err) {
    console.error(err);
  }
}

function renderKPIs(d) {
  document.getElementById("as-of").textContent =
    "Fecha de referencia del sistema (as of): " + d.as_of;
  const t = d.totales;
  document.getElementById("kpi-empleados").textContent = t.empleados;
  document.getElementById("kpi-activos").textContent = t.casos_activos;
  document.getElementById("kpi-cerrados").textContent = t.casos_cerrados;
  document.getElementById("kpi-alertas").textContent = t.alertas_riesgo_alto;
}

function renderAreas(d) {
  const tbody = document.getElementById("areas-tbody");
  tbody.innerHTML = "";
  const maxAusentismo = Math.max(...d.por_area.map((a) => a.pct_ausentismo), 1);
  d.por_area.forEach((a) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${a.area}</td>
      <td class="mono">${a.total_empleados}</td>
      <td class="mono">${a.pct_ausentismo}%
        <span class="bar-track"><span class="bar-fill" style="width:${(a.pct_ausentismo / maxAusentismo) * 90}px"></span></span>
      </td>
      <td class="mono">${a.casos_activos}</td>
      <td class="mono">${a.casos_cerrados}</td>
      <td class="mono">${a.alertas_riesgo_alto > 0 ? a.alertas_riesgo_alto : "—"}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function renderCasos() {
  const data = await api("/api/casos");
  const tbody = document.getElementById("casos-tbody");
  const thead = document.getElementById("casos-thead");
  tbody.innerHTML = "";

  if (state.role === "HRBP") {
    thead.innerHTML = `<tr><th>Área</th><th>Inicio</th><th>Fin</th><th>Días</th><th>Estado</th></tr>`;
    document.getElementById("casos-rbac-note").textContent =
      "Vista HRBP: nombre del colaborador, diagnóstico CIE-10 y categoría de salud.";
  } else {
    thead.innerHTML = `<tr><th>Empleado</th><th>Área</th><th>Diagnóstico</th><th>Inicio</th><th>Días</th><th>Estado</th></tr>`;
    document.getElementById("casos-rbac-note").textContent =
      "Vista Médico: acceso completo a diagnóstico e identidad del colaborador.";
  }

  data.casos.slice(0, 25).forEach((c) => {
    const tr = document.createElement("tr");
    if (state.role === "HRBP") {
      tr.innerHTML = `
        <td>${c.area_trabajo}</td>
        <td class="mono">${c.fecha_inicio}</td>
        <td class="mono">${c.fecha_fin}</td>
        <td class="mono">${c.dias_ausencia}</td>
        <td><span class="pill ${c.estado_caso.toLowerCase()}">${c.estado_caso}</span></td>
      `;
    } else {
      tr.innerHTML = `
        <td>${c.nombre_completo} <span class="mono" style="color:var(--ink-soft)">${c.id_empleado}</span></td>
        <td>${c.area_trabajo}</td>
        <td>${c.diagnostico_medico_confidencial} <span class="mono" style="color:var(--ink-soft)">${c.codigo_cie10 || ""}</span></td>
        <td class="mono">${c.fecha_inicio}</td>
        <td class="mono">${c.dias_ausencia}</td>
        <td><span class="pill ${c.estado_caso.toLowerCase()}">${c.estado_caso}</span></td>
      `;
    }
    tbody.appendChild(tr);
  });
}

async function renderAlertas() {
  const data = await api("/api/alertas");
  const container = document.getElementById("alertas-container");
  container.innerHTML = "";

  if (state.role === "HRBP") {
    const areas = Object.entries(data.alertas_por_area);
    if (areas.length === 0) {
      container.innerHTML = `<div class="empty-state">No hay alertas de riesgo alto activas.</div>`;
      return;
    }
    const table = document.createElement("table");
    table.innerHTML = `<thead><tr><th>Área</th><th>Alertas de Riesgo Alto (conteo)</th></tr></thead>`;
    const tbody = document.createElement("tbody");
    areas.forEach(([area, n]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${area}</td><td class="mono">${n}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    container.appendChild(table);
    return;
  }

  // MEDICO: detalle completo + acción de agendar
  if (data.alertas.length === 0) {
    container.innerHTML = `<div class="empty-state">No hay alertas de riesgo alto activas.</div>`;
    return;
  }
  const table = document.createElement("table");
  table.innerHTML = `<thead><tr><th>Empleado</th><th>Área</th><th>Motivo</th><th>Estado</th><th>Acción</th></tr></thead>`;
  const tbody = document.createElement("tbody");
  data.alertas.forEach((a) => {
    const tr = document.createElement("tr");
    tr.className = "alert-row riesgo-alto";
    const estadoLower = a.estado.toLowerCase();
    const puedeAgendar = a.estado === "PENDIENTE";
    tr.innerHTML = `
      <td>${a.nombre_completo} <span class="mono" style="color:var(--ink-soft)">${a.id_empleado}</span></td>
      <td>${a.area_trabajo}</td>
      <td style="max-width:320px">${a.motivo}</td>
      <td><span class="pill ${estadoLower}">${a.estado}${a.fecha_agendamiento ? " · " + a.fecha_agendamiento : ""}</span></td>
      <td>
        <button class="btn-small" data-id="${a.id_alerta}" ${puedeAgendar ? "" : "disabled"}>
          ${puedeAgendar ? "Agendar examen" : "—"}
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);

  container.querySelectorAll("button[data-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-id");
      const fecha = prompt("Fecha del examen médico (YYYY-MM-DD):", "2026-03-05");
      if (!fecha) return;
      try {
        await api(`/api/alertas/${id}/agendar`, {
          method: "POST",
          body: JSON.stringify({ fecha_examen: fecha }),
        });
        await renderAlertas();
        await renderApp();
      } catch (err) {
        alert("No se pudo agendar: " + err.message);
      }
    });
  });
}

async function renderEtlLog() {
  const rows = await api("/api/etl/log");
  const container = document.getElementById("etl-log-container");
  container.innerHTML = "";
  rows.slice(0, 3).forEach((r) => {
    const div = document.createElement("div");
    div.className = "etl-log-item";
    div.innerHTML = `
      <span class="src">${r.fuente}</span>
      <span>leídos: ${r.registros_leidos} · cargados: ${r.registros_cargados} · descartados: ${r.registros_descartados}</span>
    `;
    container.appendChild(div);
  });
}

async function ejecutarEtl() {
  const btn = document.getElementById("btn-run-etl");
  btn.disabled = true;
  btn.textContent = "Ejecutando...";
  try {
    await api("/api/etl/ejecutar", { method: "POST" });
    await renderApp();
  } catch (err) {
    alert("Error ejecutando ETL: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Re-ejecutar ETL + Motor de Riesgo";
  }
}

function handleLogout() {
  clearSession();
  renderLogin();
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-form").addEventListener("submit", handleLogin);
  document.getElementById("logout-btn").addEventListener("click", handleLogout);
  document.getElementById("btn-run-etl").addEventListener("click", ejecutarEtl);
  document.getElementById("fill-hrbp").addEventListener("click", () => fillCreds("hrbp.lider", "Lider2026*"));
  document.getElementById("fill-medico").addEventListener("click", () => fillCreds("medico.sst", "Medico2026*"));

  loadSession();
  if (state.token) {
    renderApp();
  } else {
    renderLogin();
  }
});
