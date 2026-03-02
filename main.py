#!/usr/bin/env python3
import asyncio, json, sys, time, threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor

# ── Dependency check ─────────────────────────────────────────────────────────
_missing = []
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    _missing.append("playwright  →  pip install playwright && python3 -m playwright install chromium")
try:
    from textual.app import App, ComposeResult
    from textual.widgets import (
        Header, Footer, DataTable, Label, Button, Input,
        TextArea, Static, LoadingIndicator, Markdown, Rule
    )
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer, Center
    from textual.screen import Screen, ModalScreen
    from textual.binding import Binding
    from textual.reactive import reactive
    from rich.text import Text
    from rich.markup import escape
except ImportError:
    _missing.append("textual/rich  →  pip install textual rich")

if _missing:
    print("Faltan dependencias:\n" + "\n".join(f"  • {m}" for m in _missing))
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".config" / "diario" / "config.json"

def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}

def save_config(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── Single-thread executor for all Playwright ops ────────────────────────────
_pw_executor = ThreadPoolExecutor(max_workers=1)

async def pw(fn, *args, **kwargs):
    """Run a playwright (sync) function in the dedicated thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pw_executor, lambda: fn(*args, **kwargs))

# ── GdcanClient ──────────────────────────────────────────────────────────────
class GdcanClient:
    LOGIN_URL  = "https://www.gdcan.org/area-usuario/login"
    PORTAL_URL = "https://www.gdcan.org/area-usuario"

    def __init__(self):
        self._pw_inst   = None
        self._browser   = None
        self.page       = None
        self.practice_id  = None
        self.practice_url = None   # /ficha-practica/{pid}  — página con la lista
        self.diary_url    = None   # /ficha-practica/{pid}/diario-actividades base
        self.user_name    = ""

    def _start(self):
        self._pw_inst = sync_playwright().start()
        self._browser = self._pw_inst.chromium.launch(
            headless=True, args=["--no-sandbox"]
        )
        self.page = self._browser.new_page()

    def _login(self, dni: str, clave: str) -> bool:
        p = self.page
        p.goto(self.LOGIN_URL, timeout=30_000)
        p.wait_for_load_state("networkidle")
        p.fill("input[name='DNI']",   dni)
        p.fill("input[name='CLAVE']", clave)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle", timeout=15_000)
        if "/login" in p.url:
            return False
        self._detect_practice_id()
        try:
            el = p.query_selector(".user-name, .avatar-name, span.fw-medium, .navbar-nav .nav-item span")
            if el:
                self.user_name = el.inner_text().strip()
        except Exception:
            pass
        return True

    def _detect_practice_id(self):
        p    = self.page
        html = p.content()
        idx  = html.find("ficha-practica/")
        if idx == -1:
            p.goto(self.PORTAL_URL, timeout=20_000)
            p.wait_for_load_state("networkidle")
            html = p.content()
            idx  = html.find("ficha-practica/")
        if idx == -1:
            return
        s   = idx + len("ficha-practica/")
        e   = s
        while e < len(html) and html[e].isalnum():
            e += 1
        pid = html[s:e]
        if len(pid) > 20:
            self.practice_id  = pid
            self.practice_url = f"{self.PORTAL_URL}/ficha-practica/{pid}"
            self.diary_url    = f"{self.PORTAL_URL}/ficha-practica/{pid}/diario-actividades"

    # ── Diary list ────────────────────────────────────────────────────────
    def _get_diary_days(self) -> List[Dict]:
        if not self.practice_url:
            return []
        p = self.page
        p.goto(self.practice_url, timeout=30_000)
        p.wait_for_load_state("networkidle")
        time.sleep(1.5)

        days = []
        seen = set()

        diary_table = None
        for table in p.query_selector_all("table"):
            header_text = (table.query_selector("thead") or table).inner_text().upper()
            if "FECHA" in header_text and ("DIARIO" in header_text or "ESTADO" in header_text):
                diary_table = table
                break

        if diary_table:
            for row in diary_table.query_selector_all("tbody tr"):
                cells = row.query_selector_all("td")
                if len(cells) < 2:
                    continue
                link = row.query_selector("a[href*='diario-actividades/']")
                if not link:
                    continue
                href   = link.get_attribute("href") or ""
                url_id = href.split("diario-actividades/")[-1].split("/")[0].split("?")[0]
                if len(url_id) < 10 or url_id in seen:
                    continue
                seen.add(url_id)

                date_str = cells[1].inner_text().strip() if len(cells) > 1 else ""
                if not date_str:
                    date_str = cells[0].inner_text().strip()

                status_raw = (cells[2].inner_text().strip().lower() if len(cells) > 2 else "")
                link_text  = link.inner_text().strip().lower()
                combined   = status_raw + " " + link_text
                if "confirmado" in combined:
                    status = "confirmado"
                elif "cumplimentado" in combined:
                    status = "guardado"
                else:
                    status = "pendiente"

                days.append({"date": date_str, "url_id": url_id, "status": status})
        else:
            for link in p.query_selector_all("a[href*='diario-actividades/']"):
                href   = link.get_attribute("href") or ""
                url_id = href.split("diario-actividades/")[-1].split("/")[0].split("?")[0]
                if len(url_id) < 10 or url_id in seen:
                    continue
                seen.add(url_id)
                link_text = link.inner_text().strip().lower()
                if "confirmado" in link_text:
                    status = "confirmado"
                elif "cumplimentado" in link_text:
                    status = "guardado"
                else:
                    status = "pendiente"
                date_str = ""
                try:
                    row   = link.evaluate_handle("el => el.closest('tr')")
                    cells = row.query_selector_all("td")
                    for cell in cells:
                        t = cell.inner_text().strip()
                        if t and "/" in t and len(t) <= 12:
                            date_str = t
                            break
                except Exception:
                    pass
                days.append({"date": date_str or url_id[:8], "url_id": url_id, "status": status})

        return days

    # ── Day detail ────────────────────────────────────────────────────────
    def _get_day_detail(self, url_id: str) -> Dict:
        p = self.page
        p.goto(f"{self.diary_url}/{url_id}", timeout=30_000)
        p.wait_for_load_state("networkidle")
        time.sleep(1)

        blocks_info: List[Dict] = []
        parent_rows = p.query_selector_all("table tbody tr.parent, tr[role='row']:has(td.details-control)")
        for pr in parent_rows:
            cells = pr.query_selector_all("td")
            name  = ""
            for cell in cells:
                t = cell.inner_text().strip()
                if t and "details-control" not in (cell.get_attribute("class") or ""):
                    name = t
                    break
            btn = pr.query_selector("td.details-control i.fa-plus-square")
            if btn:
                try:
                    btn.click()
                    time.sleep(0.35)
                except Exception:
                    pass
            blocks_info.append({"name": name or f"Bloque {len(blocks_info)+1}"})

        for btn in p.query_selector_all("td.details-control i.fa-plus-square"):
            try:
                btn.click()
                time.sleep(0.25)
            except Exception:
                pass
        time.sleep(0.8)

        activities: List[Dict] = []
        for btn in p.query_selector_all(".btn-editar-actividad"):
            raw = btn.get_attribute("data-actividad") or "{}"
            try:
                d = json.loads(raw)
                activities.append({
                    "id":          d.get("ID_ACTIVIDAD"),
                    "titulo":      d.get("TITULO", ""),
                    "descripcion": d.get("DENOMINACION", ""),
                    "accion":      d.get("ACCION", "CREAR"),
                    "diario_id":   d.get("ID_UNICO", ""),
                })
            except Exception:
                continue

        can_confirm = bool(p.query_selector("#FormConfirmarDiario"))

        title = ""
        for sel in ["h4", "h3", ".card-title", ".page-title", "h2", "h1"]:
            el = p.query_selector(sel)
            if el:
                title = el.inner_text().strip()
                if title:
                    break

        return {
            "url_id":      url_id,
            "title":       title,
            "activities":  activities,
            "can_confirm": can_confirm,
        }

    # ── Save activity ─────────────────────────────────────────────────────
    def _save_activity(self, url_id: str, id_actividad: int, descripcion: str) -> str:
        """Returns 'ok' or an error message."""
        p = self.page
        p.goto(f"{self.diary_url}/{url_id}", timeout=30_000)
        p.wait_for_load_state("networkidle")
        time.sleep(1)
        for btn in p.query_selector_all("td.details-control i.fa-plus-square"):
            try:
                btn.click()
                time.sleep(0.25)
            except Exception:
                pass
        time.sleep(0.8)

        target = None
        for btn in p.query_selector_all(".btn-editar-actividad"):
            raw = btn.get_attribute("data-actividad") or "{}"
            try:
                if json.loads(raw).get("ID_ACTIVIDAD") == id_actividad:
                    target = btn
                    break
            except Exception:
                continue
        if not target:
            return "Actividad no encontrada en la página"

        target.click()
        time.sleep(1.5)
        try:
            p.wait_for_selector("#ActividadModal.show", timeout=5_000)
        except Exception:
            return "El modal no se abrió"

        ta = p.wait_for_selector("#modal_actividad_descripcion_tareas", timeout=3_000)
        ta.click()
        p.keyboard.press("Control+a")
        p.keyboard.press("Delete")
        time.sleep(0.2)
        ta.fill(descripcion)
        time.sleep(0.3)

        save = p.query_selector("#form-edit-actividad-diario-alumno button[type='submit']")
        if not save:
            return "Botón guardar no encontrado"
        save.click()
        time.sleep(2)
        try:
            p.wait_for_selector("#ActividadModal.show", state="hidden", timeout=5_000)
        except Exception:
            pass
        return "ok"

    # ── Confirm day ───────────────────────────────────────────────────────
    def _confirm_day(self, url_id: str) -> str:
        p = self.page
        p.goto(f"{self.diary_url}/{url_id}", timeout=30_000)
        p.wait_for_load_state("networkidle")
        time.sleep(1)
        form = p.query_selector("#FormConfirmarDiario")
        if not form:
            return "No hay formulario de confirmación en esta página"
        btn = form.query_selector("button[type='submit'], input[type='submit']")
        if not btn:
            return "Botón confirmar no encontrado"
        btn.click()
        p.wait_for_load_state("networkidle", timeout=15_000)
        time.sleep(1)
        return "ok"

    def _close(self):
        try:
            self.page.close()
            self._browser.close()
            self._pw_inst.stop()
        except Exception:
            pass

# ── Helpers ───────────────────────────────────────────────────────────────────
def _week_key(date_str: str) -> Optional[Tuple[int, int]]:
    """Return (year, iso_week) for 'DD/MM/YYYY', or None on error."""
    try:
        dt  = datetime.strptime(date_str.strip(), "%d/%m/%Y")
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    except Exception:
        return None

def _now_stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")

# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: $surface;
}

/* ── Login ─────────────────────────────────── */
LoginScreen {
    align: center middle;
}
#login-box {
    width: 64;
    padding: 2 3;
    border: round $primary;
    background: $surface-darken-1;
}
#login-title {
    text-align: center;
    color: $accent;
    text-style: bold;
    padding-bottom: 1;
    width: 100%;
}
#login-subtitle {
    text-align: center;
    color: $text-muted;
    padding-bottom: 1;
    width: 100%;
}
#login-status {
    text-align: center;
    margin-top: 1;
    width: 100%;
    height: 1;
}
#login-buttons {
    margin-top: 1;
    width: 100%;
    align: center middle;
    height: 3;
}
#login-buttons Button {
    margin: 0 1;
}

/* ── Dashboard ─────────────────────────────── */
#dash-header {
    height: 3;
    padding: 0 2;
    background: $primary-darken-2;
    color: $text;
    content-align: left middle;
}
#dash-table-container {
    height: 1fr;
    padding: 0 1;
}
#dash-metrics {
    height: 3;
    padding: 0 1;
    background: $surface-darken-1;
}
.metric {
    width: 1fr;
    height: 3;
    content-align: center middle;
    border: round $primary-darken-2;
    margin: 0 1;
}
#metric-pending {
    color: ansi_bright_yellow;
}
#metric-guardado {
    color: ansi_bright_green;
}
#metric-confirmado {
    color: ansi_bright_cyan;
}
#dash-status {
    height: 1;
    padding: 0 2;
    color: $text-muted;
    background: $surface-darken-1;
}

/* ── Day Screen ─────────────────────────────── */
#day-header {
    height: 3;
    padding: 0 2;
    background: $primary-darken-2;
    color: $text;
    content-align: left middle;
}
#day-main {
    height: 1fr;
    width: 100%;
}
#day-scroll {
    width: 3fr;
    height: 100%;
    border-right: solid $primary-darken-2;
}
#day-status {
    height: 1;
    padding: 0 2;
    color: $text-muted;
    background: $surface-darken-1;
}

/* ── Preview Panel ──────────────────────────── */
#day-preview-panel {
    width: 2fr;
    height: 100%;
    padding: 1 2;
    background: $surface-darken-2;
}
#preview-label {
    color: $text-muted;
    text-align: center;
    padding-bottom: 1;
    width: 100%;
}
#preview-titulo {
    color: $accent;
    padding-bottom: 1;
    width: 100%;
    height: auto;
}
#preview-scroll {
    height: 1fr;
    width: 100%;
    padding-top: 1;
}
#preview-desc {
    color: $text;
    width: 100%;
}

/* ── Edit Modal ────────────────────────────── */
EditModal {
    align: center middle;
}
#edit-box {
    width: 84;
    height: 26;
    border: round $primary;
    background: $surface-darken-1;
    padding: 1 2;
}
#edit-title {
    color: $accent;
    text-style: bold;
    padding-bottom: 1;
    width: 100%;
}
#edit-subtitle {
    color: $text-muted;
    padding-bottom: 1;
    width: 100%;
}
#edit-textarea {
    height: 10;
    width: 100%;
    border: round $primary-darken-1;
}
#char-counter {
    height: 1;
    text-align: right;
    width: 100%;
    padding-right: 1;
}
#edit-status {
    height: 1;
    margin-top: 1;
    color: $text-muted;
    width: 100%;
}
#edit-buttons {
    margin-top: 1;
    height: 3;
    align: right middle;
    width: 100%;
}
#edit-buttons Button {
    margin-left: 1;
}

/* ── Confirm Modal ─────────────────────────── */
ConfirmModal {
    align: center middle;
}
#confirm-box {
    width: 60;
    height: 14;
    border: round $warning;
    background: $surface-darken-1;
    padding: 2 3;
}
#confirm-title {
    color: $warning;
    text-style: bold;
    text-align: center;
    width: 100%;
    padding-bottom: 1;
}
#confirm-body {
    text-align: center;
    width: 100%;
    padding-bottom: 1;
}
#confirm-buttons {
    align: center middle;
    height: 3;
    width: 100%;
    margin-top: 1;
}
#confirm-buttons Button {
    margin: 0 1;
}

/* ── Help Modal ────────────────────────────── */
HelpModal {
    align: center middle;
}
#help-box {
    width: 78;
    height: 24;
    border: round $primary;
    background: $surface-darken-1;
    padding: 1 2;
}
#help-title {
    width: 100%;
    text-style: bold;
    color: $accent;
    padding-bottom: 1;
}
#help-markdown {
    width: 100%;
    height: 1fr;
    padding: 0 1;
}
#help-buttons {
    width: 100%;
    height: 3;
    align: right middle;
}
"""

# ── Status helpers ────────────────────────────────────────────────────────────
STATUS_ICON = {
    "confirmado": "🔒",
    "guardado":   "✅",
    "pendiente":  "⬜",
}
STATUS_COLOR = {
    "confirmado": "bright_cyan",
    "guardado":   "bright_green",
    "pendiente":  "bright_yellow",
}

def status_text(s: str) -> Text:
    icon  = STATUS_ICON.get(s, "❓")
    color = STATUS_COLOR.get(s, "white")
    label = s.capitalize()
    t = Text()
    t.append(f"{icon} {label}", style=color)
    return t


class HelpModal(ModalScreen):
    """Modal simple de ayuda y atajos."""

    BINDINGS = [Binding("escape,h", "dismiss", "Cerrar")]

    def __init__(self, title: str, markdown_text: str):
        super().__init__()
        self._title = title
        self._markdown_text = markdown_text

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label(f"❓ {self._title}", id="help-title")
            yield Markdown(self._markdown_text, id="help-markdown")
            with Horizontal(id="help-buttons"):
                yield Button("Cerrar [Esc]", id="btn-help-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-help-close":
            self.dismiss(None)

# ── Edit Modal ────────────────────────────────────────────────────────────────
class EditModal(ModalScreen):
    """Modal para editar la descripción de una actividad."""

    BINDINGS = [Binding("escape", "dismiss", "Cancelar")]

    def __init__(self, client: GdcanClient, url_id: str, activity: Dict):
        super().__init__()
        self.client   = client
        self.url_id   = url_id
        self.activity = activity

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-box"):
            yield Label(f"✏️  {escape(self.activity['titulo'])}", id="edit-title")
            accion = self.activity.get("accion", "CREAR")
            yield Label(
                f"{'Editando entrada existente' if accion == 'EDITAR' else 'Creando nueva entrada'}  •  ID {self.activity['id']}",
                id="edit-subtitle"
            )
            yield TextArea(
                self.activity.get("descripcion", ""),
                id="edit-textarea",
                language=None,
            )
            yield Label("", id="char-counter")
            yield Label("", id="edit-status")
            with Horizontal(id="edit-buttons"):
                yield Button("Guardar [Ctrl+S]", id="btn-save", variant="success")
                yield Button("Cancelar [Esc]",   id="btn-cancel", variant="default")

    def on_mount(self):
        ta = self.query_one("#edit-textarea", TextArea)
        ta.focus()
        self._update_char_counter(len(ta.text))

    def _update_char_counter(self, n: int) -> None:
        if n >= 100:
            t = Text(f"{n} caracteres", style="bright_green")
        elif n >= 30:
            t = Text(f"{n} caracteres", style="bright_yellow")
        elif n > 0:
            t = Text(f"{n} caracteres", style="bright_red")
        else:
            t = Text("0 caracteres", style="dim")
        self.query_one("#char-counter", Label).update(t)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        n = len(self.query_one("#edit-textarea", TextArea).text)
        self._update_char_counter(n)

    def on_key(self, event) -> None:
        if event.key == "ctrl+s":
            self._save()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-save":
            self._save()

    def _save(self):
        desc = self.query_one("#edit-textarea", TextArea).text.strip()
        if not desc:
            self.query_one("#edit-status", Label).update("⚠️  La descripción no puede estar vacía")
            return
        status_lbl = self.query_one("#edit-status", Label)
        status_lbl.update("⏳ Guardando...")
        self.query_one("#btn-save", Button).disabled = True

        async def worker():
            result = await pw(self.client._save_activity, self.url_id, self.activity["id"], desc)
            if result == "ok":
                self.dismiss({"id": self.activity["id"], "descripcion": desc})
            else:
                status_lbl.update(f"❌ {result}")
                self.query_one("#btn-save", Button).disabled = False

        self.run_worker(worker())


# ── Confirm Modal ─────────────────────────────────────────────────────────────
class ConfirmModal(ModalScreen):
    """Modal de confirmación antes de confirmar el día."""

    BINDINGS = [Binding("escape", "dismiss", "Cancelar")]

    def __init__(self, client: GdcanClient, url_id: str, date: str):
        super().__init__()
        self.client = client
        self.url_id = url_id
        self.date   = date

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("⚠️  CONFIRMAR DIARIO", id="confirm-title")
            yield Label(
                f"¿Confirmar el diario del {self.date}?\n\n"
                "Esta acción es IRREVERSIBLE. Una vez confirmado,\n"
                "no podrás editar las actividades de este día.",
                id="confirm-body"
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Sí, confirmar", id="btn-yes", variant="error")
                yield Button("Cancelar",      id="btn-no",  variant="default")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-no":
            self.dismiss(False)
        elif event.button.id == "btn-yes":
            self._confirm()

    def _confirm(self):
        self.query_one("#btn-yes", Button).disabled = True
        self.query_one("#btn-no", Button).disabled  = True
        self.query_one("#confirm-body", Label).update("⏳ Confirmando...")

        async def worker():
            result = await pw(self.client._confirm_day, self.url_id)
            self.dismiss(result == "ok")

        self.run_worker(worker())


# ── Day Screen ────────────────────────────────────────────────────────────────
class DayScreen(Screen):
    """Vista detallada de un día — actividades por bloque con panel de previsualización."""

    BINDINGS = [
        Binding("escape,b",  "go_back",      "Volver"),
        Binding("c",         "confirm",      "Confirmar día"),
        Binding("r",         "refresh",      "Recargar"),
        Binding("n",         "next_empty",   "Siguiente vacía"),
        Binding("h",         "show_help",    "Ayuda"),
    ]

    def __init__(self, client: GdcanClient, url_id: str, date: str):
        super().__init__()
        self.client  = client
        self.url_id  = url_id
        self.date    = date
        self._detail: Optional[Dict] = None
        self._activity_widgets: List  = []
        self._row_order: List[Tuple[str, bool]] = []  # (key, is_real_activity)
        self._highlighted_key: str = ""

    def compose(self) -> ComposeResult:
        yield Static(f"📅  {self.date}", id="day-header")
        with Horizontal(id="day-main"):
            with ScrollableContainer(id="day-scroll"):
                yield LoadingIndicator(id="day-loading")
                yield Static("", id="day-content")
            with Vertical(id="day-preview-panel"):
                yield Static("── Vista previa ──", id="preview-label")
                yield Static("", id="preview-titulo")
                yield Rule()
                with ScrollableContainer(id="preview-scroll"):
                    yield Static(
                        "Selecciona una actividad\npara ver su descripción completa.",
                        id="preview-desc"
                    )
        yield Static("Cargando...", id="day-status")
        yield Footer()

    def on_mount(self):
        self.run_worker(self._load_detail())

    async def _load_detail(self):
        self._set_status("⏳ Cargando actividades...")
        try:
            detail = await pw(self.client._get_day_detail, self.url_id)
            self._detail = detail
            await self._render_detail(detail)
            acts   = detail["activities"]
            filled = sum(1 for a in acts if a["accion"] == "EDITAR")
            empty  = len(acts) - filled
            tip    = " · c: confirmar" if detail.get("can_confirm") else ""
            self._set_status(
                f"  {len(acts)} actividades · ✅ {filled} rellenas · ⬜ {empty} vacías"
                f" · Enter: editar · n: siguiente vacía · Esc: volver{tip}"
            )
        except Exception as e:
            self._set_status(f"❌ Error: {e}")

    async def _render_detail(self, detail: Dict, restore_key: Optional[str] = None):
        # Remove existing widgets
        for wid in ("#day-content", "#day-loading", "#day-table"):
            try:
                await self.query_one(wid).remove()
            except Exception:
                pass

        acts = detail["activities"]
        scroll = self.query_one("#day-scroll")
        if not acts:
            await scroll.mount(Static("No se encontraron actividades.", id="day-content"))
            return

        table = DataTable(id="day-table", cursor_type="row")
        table.zebra_stripes = True
        table.add_columns("", "Actividad", "Descripción")
        self._activity_widgets = []
        self._row_order = []

        prev_block = -1
        for act in acts:
            aid = act["id"] or 0
            if   aid <= 920767: block = 1
            elif aid <= 920770: block = 2
            elif aid <= 920772: block = 3
            else:               block = 4

            if block != prev_block:
                block_names = {1: "PLANIFICACIÓN", 2: "EJECUCIÓN", 3: "REVISIÓN", 4: "CORRECCIÓN"}
                bkey = f"__block_{block}__"
                table.add_row(
                    Text(f"── Bloque {block}: {block_names.get(block, '')} ──", style="bold bright_blue"),
                    Text(""), Text(""),
                    key=bkey,
                )
                self._row_order.append((bkey, False))
                prev_block = block

            icon       = "✅" if act["accion"] == "EDITAR" else "⬜"
            titulo     = act["titulo"] or f"Actividad {act['id']}"
            desc       = act["descripcion"] or "(sin descripción)"
            desc_short = desc[:55] + "…" if len(desc) > 55 else desc
            akey       = str(act["id"])

            table.add_row(
                Text(icon),
                Text(titulo, style="bold" if act["accion"] == "EDITAR" else ""),
                Text(desc_short, style="bright_green" if act["accion"] == "EDITAR" else "dim"),
                key=akey,
            )
            self._row_order.append((akey, True))
            self._activity_widgets.append(act)

        await scroll.mount(table)

        # Find target row (restore position or go to first activity)
        target_idx   = None
        first_act_idx = None
        first_act    = None
        target_act   = None
        for i, (key, is_act) in enumerate(self._row_order):
            if is_act:
                if first_act_idx is None:
                    first_act_idx = i
                    first_act = next((a for a in self._activity_widgets if str(a["id"]) == key), None)
                if key == restore_key:
                    target_idx = i
                    target_act = next((a for a in self._activity_widgets if str(a["id"]) == key), None)
                    break

        move_to = target_idx if target_idx is not None else (first_act_idx or 0)
        if move_to > 0:
            table.move_cursor(row=move_to)

        # Eagerly update preview for the target activity
        preview_act = target_act if target_act is not None else first_act
        if preview_act:
            self._update_preview(preview_act)

        table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.row_key is None:
            return
        key = str(event.row_key.value)
        self._highlighted_key = key
        if key.startswith("__block_"):
            return
        act = next((a for a in (self._activity_widgets or []) if str(a["id"]) == key), None)
        if act:
            self._update_preview(act)

    def _update_preview(self, act: Dict):
        try:
            titulo = act["titulo"] or f"Actividad {act['id']}"
            accion_str = "✅ Rellena" if act["accion"] == "EDITAR" else "⬜ Sin rellenar"

            titulo_text = Text()
            titulo_text.append(f"{accion_str}\n", style="")
            titulo_text.append(titulo, style="bold")
            self.query_one("#preview-titulo", Static).update(titulo_text)

            desc = act["descripcion"] or "(sin descripción — pulsa Enter para añadir)"
            self.query_one("#preview-desc", Static).update(Text(desc))
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        key = str(event.row_key.value)
        if key.startswith("__block_"):
            return
        act = next((a for a in (self._activity_widgets or []) if str(a["id"]) == key), None)
        if act:
            self._edit_activity(act)

    def _edit_activity(self, act: Dict):
        async def after_edit(result):
            if result:
                for a in (self._detail["activities"] if self._detail else []):
                    if a["id"] == result["id"]:
                        a["descripcion"] = result["descripcion"]
                        a["accion"]      = "EDITAR"
                await self._render_detail(self._detail, restore_key=str(result["id"]))
                self.app.notify(
                    "Actividad guardada correctamente",
                    title="✅ Guardado",
                    timeout=3,
                )
        self.app.push_screen(EditModal(self.client, self.url_id, act), after_edit)

    def action_go_back(self):
        self.app.pop_screen()

    def action_confirm(self):
        if not self._detail or not self._detail.get("can_confirm"):
            self._set_status("⚠️  Este día no se puede confirmar todavía")
            return

        async def after_confirm(ok):
            if ok:
                self._set_status("🔒 Diario confirmado correctamente")
                if self._detail:
                    self._detail["can_confirm"] = False
                self.app.notify(
                    f"El diario del {self.date} ha sido confirmado",
                    title="🔒 Confirmado",
                    timeout=4,
                )
            else:
                self._set_status("❌ No se pudo confirmar")

        self.app.push_screen(
            ConfirmModal(self.client, self.url_id, self.date),
            after_confirm
        )

    def action_next_empty(self):
        """Mueve el cursor a la siguiente actividad sin rellenar."""
        acts = self._activity_widgets or []
        empty_ids = {str(a["id"]) for a in acts if a["accion"] == "CREAR"}
        if not empty_ids:
            self.app.notify("Todas las actividades están rellenas", timeout=2)
            return
        try:
            table   = self.query_one("#day-table", DataTable)
            cur_row = table.cursor_coordinate.row
            # Look for next empty after current row
            for i, (key, is_act) in enumerate(self._row_order):
                if is_act and key in empty_ids and i > cur_row:
                    table.move_cursor(row=i)
                    return
            # Wrap around — find first empty from top
            for i, (key, is_act) in enumerate(self._row_order):
                if is_act and key in empty_ids:
                    table.move_cursor(row=i)
                    return
        except Exception:
            pass

    async def action_refresh(self):
        try:
            await self.query_one("#day-table").remove()
        except Exception:
            pass
        await self.query_one("#day-scroll").mount(LoadingIndicator(id="day-loading"))
        self.run_worker(self._load_detail())

    def action_show_help(self):
        self.app.push_screen(HelpModal(
            "Atajos — detalle de día",
            """
- **Enter**: editar actividad
- **n**: saltar a la siguiente actividad vacía
- **c**: confirmar día
- **r**: recargar
- **Esc** o **b**: volver
- **h**: abrir/cerrar ayuda
            """.strip()
        ))

    def _set_status(self, msg: str):
        self.query_one("#day-status", Static).update(f"  {msg} · {_now_stamp()}")


# ── Dashboard Screen ──────────────────────────────────────────────────────────
class DashboardScreen(Screen):
    """Pantalla principal — lista de todos los días del diario."""

    BINDINGS = [
        Binding("v",         "view_day",  "Ver día"),
        Binding("c",         "confirm",   "Confirmar"),
        Binding("r",         "refresh",   "Recargar"),
        Binding("h",         "show_help", "Ayuda"),
        Binding("q,ctrl+c",  "quit",      "Salir"),
    ]

    def __init__(self, client: GdcanClient):
        super().__init__()
        self.client = client
        self._days:  List[Dict] = []
        self._current_url_id: str = ""

    def compose(self) -> ComposeResult:
        name = self.client.user_name or "Usuario"
        yield Static(
            f"📖  Diario de Prácticas  [{name}]",
            id="dash-header"
        )
        with Horizontal(id="dash-metrics"):
            yield Static("⬜ Pendientes: 0", id="metric-pending", classes="metric")
            yield Static("✅ Guardados: 0", id="metric-guardado", classes="metric")
            yield Static("🔒 Confirmados: 0", id="metric-confirmado", classes="metric")
        with Container(id="dash-table-container"):
            yield LoadingIndicator(id="dash-loading")
        yield Static("Cargando días...", id="dash-status")
        yield Footer()

    def on_mount(self):
        self.run_worker(self._load_days())

    async def _load_days(self):
        self._set_status("⏳ Obteniendo lista de días...")
        try:
            days = await pw(self.client._get_diary_days)
            self._days = days
            self._render_table(days)
            pending   = sum(1 for d in days if d["status"] == "pendiente")
            guardados = sum(1 for d in days if d["status"] == "guardado")
            confirmed = sum(1 for d in days if d["status"] == "confirmado")
            self.query_one("#metric-pending", Static).update(f"⬜ Pendientes: {pending}")
            self.query_one("#metric-guardado", Static).update(f"✅ Guardados: {guardados}")
            self.query_one("#metric-confirmado", Static).update(f"🔒 Confirmados: {confirmed}")
            # Update header with live stats
            name = self.client.user_name or "Usuario"
            self.query_one("#dash-header", Static).update(
                f"📖  Diario  [{name}]  "
                f"·  ⬜ {pending} pendientes  "
                f"·  ✅ {guardados} guardados  "
                f"·  🔒 {confirmed} confirmados"
            )
            self._set_status(
                f"  {len(days)} días  ·  Enter: ver · c: confirmar · r: recargar · q: salir"
            )
        except Exception as e:
            self._set_status(f"❌ Error cargando días: {e}")

    def _render_table(self, days: List[Dict]):
        try:
            self.query_one("#dash-loading").remove()
        except Exception:
            pass
        try:
            self.query_one("#dash-table").remove()
        except Exception:
            pass

        table = DataTable(id="dash-table", cursor_type="row")
        table.zebra_stripes = True
        table.add_columns("Fecha", "Estado", "")

        prev_week: Optional[Tuple[int, int]] = None
        for i, d in enumerate(days):
            week = _week_key(d["date"])
            # Insert week separator when the ISO week changes
            if week and week != prev_week and prev_week is not None:
                sep_text = Text(f"  semana {week[1]}", style="dim italic")
                table.add_row(
                    sep_text, Text(""), Text(""),
                    key=f"__sep_{i}__",
                )
            prev_week = week

            table.add_row(
                Text(d["date"], style="bold"),
                status_text(d["status"]),
                Text("↵ ver", style="dim"),
                key=d["url_id"],
            )

        self.query_one("#dash-table-container").mount(table)
        table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.row_key is not None:
            val = str(event.row_key.value)
            if not val.startswith("__"):
                self._current_url_id = val

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        url_id = str(event.row_key.value)
        if url_id.startswith("__"):
            return
        day = next((d for d in self._days if d["url_id"] == url_id), None)
        if day:
            self.app.push_screen(DayScreen(self.client, url_id, day["date"]))

    def action_view_day(self):
        url_id = self._current_url_id
        if not url_id:
            return
        day = next((d for d in self._days if d["url_id"] == url_id), None)
        if day:
            self.app.push_screen(DayScreen(self.client, url_id, day["date"]))

    def action_confirm(self):
        url_id = self._current_url_id
        if not url_id:
            return
        day = next((d for d in self._days if d["url_id"] == url_id), None)
        if not day:
            return
        if day["status"] == "confirmado":
            self._set_status("ℹ️  Este día ya está confirmado")
            return

        async def after(ok: bool):
            if ok:
                day["status"] = "confirmado"
                self._render_table(self._days)
                self._set_status(f"🔒 Día {day['date']} confirmado")
                self.app.notify(
                    f"Diario del {day['date']} confirmado",
                    title="🔒",
                    timeout=3,
                )
            else:
                self._set_status("❌ No se pudo confirmar")

        self.app.push_screen(
            ConfirmModal(self.client, url_id, day["date"]),
            after
        )

    def action_refresh(self):
        self.run_worker(self._load_days())

    def action_show_help(self):
        self.app.push_screen(HelpModal(
            "Atajos — pantalla principal",
            """
- **Enter** o **v**: abrir día
- **c**: confirmar día seleccionado
- **r**: recargar listado
- **q** / **Ctrl+C**: salir
- **h**: abrir/cerrar ayuda
            """.strip()
        ))

    def action_quit(self):
        self.app.exit()

    def _set_status(self, msg: str):
        self.query_one("#dash-status", Static).update(f"  {msg} · {_now_stamp()}")


# ── Login Screen ──────────────────────────────────────────────────────────────
class LoginScreen(Screen):
    """Pantalla de login."""

    BINDINGS = [Binding("escape", "quit", "Salir"), Binding("h", "show_help", "Ayuda")]

    def __init__(self, client: GdcanClient):
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="login-box"):
                yield Label("📖  Diario de Prácticas", id="login-title")
                yield Label("gdcan.org", id="login-subtitle")
                yield Rule()
                yield Label("DNI:")
                yield Input(placeholder="12345678X", id="dni-input")
                yield Label("Contraseña:")
                yield Input(placeholder="••••••••", password=True, id="pass-input")
                with Horizontal(id="login-buttons"):
                    yield Button("Entrar", id="btn-login", variant="primary")
                    yield Button("Salir",  id="btn-quit",  variant="default")
                yield Label("", id="login-status")
        yield Footer()

    def on_mount(self):
        cfg = load_config()
        if cfg.get("dni"):
            self.query_one("#dni-input", Input).value = cfg["dni"]
        if cfg.get("clave"):
            self.query_one("#pass-input", Input).value = cfg["clave"]
        if cfg.get("dni") and cfg.get("clave"):
            self.run_worker(self._do_login(cfg["dni"], cfg["clave"]))
        else:
            self.query_one("#dni-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn-quit":
            self.app.exit()
        elif event.button.id == "btn-login":
            self._trigger_login()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "dni-input":
            self.query_one("#pass-input", Input).focus()
        elif event.input.id == "pass-input":
            self._trigger_login()

    def _trigger_login(self):
        dni   = self.query_one("#dni-input", Input).value.strip()
        clave = self.query_one("#pass-input", Input).value.strip()
        if not dni or not clave:
            self.query_one("#login-status", Label).update("⚠️  Introduce DNI y contraseña")
            return
        self.run_worker(self._do_login(dni, clave))

    def action_show_help(self):
        self.app.push_screen(HelpModal(
            "Ayuda — login",
            """
- Escribe tu **DNI** y **contraseña**
- Pulsa **Enter** en contraseña o botón **Entrar**
- **Esc**: salir
- **h**: abrir/cerrar ayuda
            """.strip()
        ))

    async def _do_login(self, dni: str, clave: str):
        status = self.query_one("#login-status", Label)
        try:
            btn_login = self.query_one("#btn-login", Button)
            btn_login.disabled = True
        except Exception:
            pass
        status.update("⏳ Iniciando sesión...")
        try:
            ok = await pw(self.client._login, dni, clave)
            if ok:
                save_config({"dni": dni, "clave": clave})
                status.update("✅ Sesión iniciada. Cargando...")
                await asyncio.sleep(0.4)
                self.app.switch_screen(DashboardScreen(self.client))
            else:
                status.update("❌ Credenciales incorrectas")
                try:
                    self.query_one("#btn-login", Button).disabled = False
                except Exception:
                    pass
        except Exception as e:
            status.update(f"❌ Error: {e}")
            try:
                self.query_one("#btn-login", Button).disabled = False
            except Exception:
                pass


# ── App ───────────────────────────────────────────────────────────────────────
class DiarioApp(App):
    CSS = CSS
    TITLE = "Diario gdcan.org"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Salir", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.client = GdcanClient()

    def on_mount(self):
        async def start():
            await pw(self.client._start)
            self.push_screen(LoginScreen(self.client))
        self.run_worker(start())

    def action_quit(self):
        self.exit()

    def on_unmount(self):
        try:
            _pw_executor.submit(self.client._close)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Diario de prácticas gdcan.org TUI")
    parser.add_argument("--reset", action="store_true", help="Borrar credenciales guardadas")
    args = parser.parse_args()

    if args.reset:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            print("Credenciales eliminadas.")
        else:
            print("No hay credenciales guardadas.")
        return

    app = DiarioApp()
    app.run()


if __name__ == "__main__":
    main()
