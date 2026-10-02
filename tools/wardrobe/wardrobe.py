#!/usr/bin/env python
"""wardrobe — base + garment LAYER maker for Game Room story packs (Qt).
Qwen-Image-2.1 through a RUNNING ComfyUI (default http://127.0.0.1:8188); no models on this side.

    conda activate wardrobe
    python tools/wardrobe/wardrobe.py --out tmp/wardrobe/sapphire                      # Base tab
    python tools/wardrobe/wardrobe.py --base tmp/wardrobe/sapphire/sapphire-base.png --out tmp/wardrobe/sapphire

Base tab:     one SOURCE image. Load it (her face) or Go from text, click a candidate, Use it, change the prompt,
              Go again. "full body from this" makes the 736×1216 body with the source as the reference.
              Keep as base → <name>-base.png.
Wardrobe tab: name the item (free text), pick its slot, Go ×N, click one, Save layer → layer-<item>.png
              (+ preview/<item>.png to browse, + the recipe and raw result in wardrobe/).
The cut lives in core.py: changed pixels inside her own alpha, so the model's edge halo never reaches a layer.
"""
import argparse, glob, json, os, sys, threading, time
import numpy as np, cv2
from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QImage, QPixmap, QIcon, QPalette, QColor, QShortcut, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton, QToolButton, QLineEdit, QSpinBox,
                               QSlider, QCheckBox, QRadioButton, QComboBox, QPlainTextEdit, QScrollArea, QTabWidget, QSplitter,
                               QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QFileDialog, QMessageBox, QSizePolicy,
                               QButtonGroup, QDialog, QTextBrowser, QGraphicsView, QGraphicsScene, QDoubleSpinBox, QColorDialog)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (Comfy, build_graph, cut, checker, stack, preview_sheet, make_icon, recolour, colour_shift, FRAME, LAYER_ORDER, SLOTS, PAIRED, STICKS_OUT, HAIR, UNDER,
                  GARMENT_TPL, SLOT_TPL, GARMENT_HINT, BASE_PROMPT, REF_PROMPT, EDIT_HINT, REGION_HINT, REGION_ADD_HINT, REGION_BLANK_HINT, crop_up, paste_down, blank_hole, paste_hole, slot_for, clean_alpha, alpha_is_soft, alpha_hist, alpha_heat, diff_view, onion, fix_alpha, on_bg, change_map, over_dim, FLAT_BGS)

NONE = "(none)"
THUMB = 128


# ---------------------------------------------------------------- widgets
def qimage(img):
    """PIL RGBA → QImage (copied, so the PIL buffer may go)."""
    img = img.convert("RGBA"); data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888).copy()


class Preview(QGraphicsView):
    """One PIL image over a checker. Fits the view until you zoom: wheel = zoom at the cursor, drag = pan,
    double-click = fit again. Linked previews share zoom and pan so side-by-side compares the same pixels."""

    def __init__(self):
        super().__init__(); self.scene_ = QGraphicsScene(self); self.setScene(self.scene_)
        self.pix = self.scene_.addPixmap(QPixmap()); self.fitted = True; self.peers = []; self._syncing = False
        self.setStyleSheet("background:#111; border:1px solid #2a2a2a"); self.setRenderHints(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag); self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored); self.setMinimumSize(96, 96)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for sb in (self.horizontalScrollBar(), self.verticalScrollBar()): sb.valueChanged.connect(lambda *_: self._push())

    def link(self, other): self.peers.append(other); other.peers.append(self)

    def visible_box(self):
        """(x0, y0, x1, y1) of the image pixels currently on screen, or None when the whole image fits."""
        if self.pix.pixmap().isNull() or self.fitted: return None
        r = self.mapToScene(self.viewport().rect()).boundingRect().intersected(self.pix.boundingRect())
        if r.width() < 16 or r.height() < 16: return None
        W, H = self.pix.pixmap().width(), self.pix.pixmap().height()
        box = (max(0, int(r.left())), max(0, int(r.top())), min(W, int(r.right())), min(H, int(r.bottom())))
        return None if (box[2] - box[0] >= W and box[3] - box[1] >= H) else box

    def set_image(self, img):
        if img is None: self.pix.setPixmap(QPixmap()); return
        self.pix.setPixmap(QPixmap.fromImage(qimage(Image.alpha_composite(checker(img.size), img.convert("RGBA")))))
        self.scene_.setSceneRect(self.pix.boundingRect())
        if self.fitted: self.fit()

    def clear_image(self): self.set_image(None)

    def fit(self):
        self.fitted = True
        if not self.pix.pixmap().isNull(): self.fitInView(self.pix, Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fitted: self.fit()

    def wheelEvent(self, e):
        if self.pix.pixmap().isNull(): return
        self.fitted = False; f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        if self.transform().m11() * f < 0.02 or self.transform().m11() * f > 40: return
        self.scale(f, f); self._push()

    def mouseDoubleClickEvent(self, e): self.fit(); self._push()

    def _push(self):
        """Mirror zoom + scroll to linked previews (same-size images only)."""
        if self._syncing: return
        for p in self.peers:
            if p.pix.pixmap().size() != self.pix.pixmap().size(): continue
            p._syncing = True
            try:
                p.fitted = self.fitted; p.setTransform(self.transform())
                p.horizontalScrollBar().setValue(self.horizontalScrollBar().value()); p.verticalScrollBar().setValue(self.verticalScrollBar().value())
            finally: p._syncing = False


class PaintView(Preview):
    """A Preview you can also paint on. In brush mode: left button erases, right restores, middle pans, wheel zooms,
    and a red ring shows the brush at the image's own scale. dab(x0, y0, x1, y1, erase, first) is called per mouse
    step in image pixels; stroke_end() on release."""

    def __init__(self, dab, stroke_end):
        super().__init__(); self.dab, self.stroke_end = dab, stroke_end; self.brush = False; self.radius = 4.0; self.last = None; self.pan = None
        self.ring = self.scene_.addEllipse(0, 0, 1, 1, QPen(QColor(255, 70, 70), 0)); self.ring.setZValue(10); self.ring.hide(); self.setMouseTracking(True)

    def set_brush(self, on):
        self.brush = on; self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag); self.ring.setVisible(on); self.last = self.pan = None

    def _at(self, e):
        p = self.mapToScene(e.position().toPoint()); return p.x(), p.y()

    def mousePressEvent(self, e):
        if self.brush and e.button() in (Qt.LeftButton, Qt.RightButton) and not self.pix.pixmap().isNull():
            x, y = self._at(e); erase = e.button() == Qt.LeftButton; self.last = (x, y, erase); self.dab(x, y, x, y, erase, True); return
        if self.brush and e.button() == Qt.MiddleButton: self.pan = e.position(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.brush:
            x, y = self._at(e); r = self.radius; self.ring.setRect(x - r, y - r, 2 * r, 2 * r)
            if self.last:
                x0, y0, erase = self.last; self.dab(x0, y0, x, y, erase, False); self.last = (x, y, erase); return
            if self.pan is not None:
                d = e.position() - self.pan; self.pan = e.position()
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y())); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.brush and self.last and e.button() in (Qt.LeftButton, Qt.RightButton): self.last = None; self.stroke_end(); return
        if self.brush and e.button() == Qt.MiddleButton: self.pan = None; return
        super().mouseReleaseEvent(e)


class Strip(QScrollArea):
    """Full-width thumbnail strip, horizontal scroll, one checkable button per result."""

    def __init__(self, on_click):
        super().__init__(); self.on_click = on_click
        self.setWidgetResizable(True); self.setFixedHeight(THUMB + 56)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget(); self.lay = QHBoxLayout(self.inner); self.lay.setAlignment(Qt.AlignLeft); self.setWidget(self.inner)
        self.group = QButtonGroup(self); self.group.setExclusive(True)

    def set(self, results, sel):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w: self.group.removeButton(w); w.deleteLater()
        for i, (label, img, e) in enumerate(results):
            t = img.copy(); t.thumbnail((THUMB, THUMB)); t = Image.alpha_composite(checker(t.size, 8), t)
            b = QToolButton(); b.setIcon(QIcon(QPixmap.fromImage(qimage(t)))); b.setIconSize(QSize(THUMB, THUMB))
            b.setText(str(label)); b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon); b.setCheckable(True); b.setChecked(i == sel)
            k = e["kind"] if isinstance(e, dict) else ""; worn = ", ".join(e.get("ctx", ())) if isinstance(e, dict) else ""
            b.setToolTip({"saved": "the layer on disk, worn — what the card shows now. Nothing to save here.",
                          "blank": "nothing saved for this item yet.",
                          "new": "a fresh result: the sliders cut it, Save writes it."}.get(k, "") + (f"\nmade over: {worn}" if worn else ""))
            b.clicked.connect(lambda _=False, i=i: self.on_click(i)); self.group.addButton(b); self.lay.addWidget(b)

    def wheelEvent(self, e):
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - e.angleDelta().y())


class Gen(QThread):
    """Runs n seeds against Comfy; one `result` per image. size=None → an edit at the reference's size."""
    result = Signal(int, object); status = Signal(str); done = Signal()

    def __init__(self, comfy, tpl, n, prompt, ref, size, res, steps, stop, negative="", cfg=1.0, post=None, extra=(), snap=True):
        super().__init__(); self.a = (comfy, tpl, n, prompt, ref, size, res, steps, stop, negative, cfg); self.post = post; self.extra = list(extra)
        self.snap = snap

    def run(self):
        comfy, tpl, n, prompt, ref, size, res, steps, stop, negative, cfg = self.a
        base_seed = int(time.time()) % 1_000_000
        try:
            ref_name = ([comfy.upload(ref)] if ref is not None else []) + [comfy.upload(x) for x in self.extra]
            for i in range(n):
                seed = base_seed + i * 7919; t0 = time.time()
                self.status.emit(f"{i + 1}/{n}  seed {seed} …")
                img = comfy.run(build_graph(tpl, prompt, seed, steps, ref_name, size, res, negative, cfg), stop)
                want = size or (ref.size if ref is not None else img.size)
                if img.size != want: img = img.resize(want, Image.LANCZOS)
                if self.snap: img = clean_alpha(img)                # the model's alpha is soft everywhere — snap it at the door
                if self.post: img = self.post(img)
                self.result.emit(seed, img)
                self.status.emit(f"{i + 1}/{n} in {time.time() - t0:.1f}s — click a thumbnail")
        except Exception as e:
            self.status.emit(str(e)[:220])
        self.done.emit()


class RefBox(QWidget):
    """[thumb] filename  [Reference…] [×] — the shared extra reference image, shown in each sidebar."""

    def __init__(self, on_pick, on_clear):
        super().__init__(); l = QHBoxLayout(self); l.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel(); self.thumb.setFixedSize(56, 56); self.thumb.setStyleSheet("background:#111; border:1px solid #2a2a2a"); self.thumb.setAlignment(Qt.AlignCenter)
        self.name = QLabel("(no reference)"); self.name.setStyleSheet("color:#8a8a8a")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)        # a long name never pushes × off screen
        b = QPushButton("Reference…"); b.clicked.connect(on_pick); x = QPushButton("×"); x.setFixedWidth(28); x.clicked.connect(on_clear)
        for w in (self.thumb, self.name, b, x): l.addWidget(w)
        l.setStretch(1, 1)
        self.setToolTip("A second image the model sees on every Go while it's set — rides along as image 2. Refer to it in the prompt: "
                        "'the same face as the second image', 'the jacket from the second image'. The source stays image 1.")

    def show(self, img, name):
        if img is None: self.thumb.setPixmap(QPixmap()); self.name.setText("(no reference)"); self.name.setToolTip(""); return
        t = img.copy(); t.thumbnail((56, 56)); self.thumb.setPixmap(QPixmap.fromImage(qimage(Image.alpha_composite(checker(t.size, 6), t))))
        self.name.setText(short(name)); self.name.setToolTip(name)


def short(name, keep=20):
    """A file name cut to `keep` characters, the middle gone, the extension kept: a long one once pushed the × off screen."""
    return name if len(name) <= keep else name[:keep // 2 - 1] + "…" + name[-(keep - keep // 2):]


def hint(text):
    l = QLabel(text); l.setWordWrap(True); l.setStyleSheet("color:#8a8a8a; font-size:11px"); return l


def tip(widget, text):
    """Hover help instead of a label under the field — keeps the panel short."""
    widget.setToolTip(text); return widget


class Help(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.setWindowTitle("wardrobe — help"); self.resize(820, 900)
        v = QVBoxLayout(self); t = QTextBrowser(); t.setOpenExternalLinks(True); v.addWidget(t)
        try: t.setMarkdown(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"), encoding="utf-8").read())
        except Exception as e: t.setPlainText(f"README.md not found: {e}")


def big(text, color, cb):
    b = QPushButton(text); b.setStyleSheet(f"background:{color}; color:white; font-weight:bold; padding:8px"); b.clicked.connect(cb); return b


def row(*items):
    w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0)
    for x in items: l.addStretch(x) if isinstance(x, int) else l.addWidget(x)
    return w


def pil_open(p): return Image.open(p).convert("RGBA")


# ---------------------------------------------------------------- app
class App(QMainWindow):
    DEFAULTS = {"edit": EDIT_HINT, "body": REF_PROMPT, "text": BASE_PROMPT, "region": REGION_HINT, "region_add": REGION_ADD_HINT, "region_blank": REGION_BLANK_HINT}

    def __init__(self, args):
        super().__init__(); self.args = args
        self.setWindowTitle("wardrobe — Qwen-Image-2.1 via ComfyUI"); self.resize(1800, 1100)
        self.tpl = json.load(open(args.workflow)); self.stop = threading.Event(); self.gen = None
        self.base = None; self.base_path = None; self.work = None; self.work_name = ""
        self.bresults = []; self.bsel = None; self.results = []; self.sel = None; self.cut_cache = None; self._gb = None; self.gens = {}; self._fresh = False
        self.item_slot = self.load_story_items(args.story); self._auto_prompt = None; self.wear_boxes = {}
        self.ref_img = None; self.ref_name = ""; self.refboxes = []
        self.cut_timer = QTimer(self); self.cut_timer.setSingleShot(True); self.cut_timer.setInterval(120); self.cut_timer.timeout.connect(self.do_cut)
        self.build()
        if args.base: self.load_base(args.base)

    # ---------- shell ----------
    def build(self):
        root = QWidget(); self.setCentralWidget(root); v = QVBoxLayout(root)
        self.url = QLineEdit(self.args.comfy); self.url.setFixedWidth(260)
        self.status = QLabel(""); self.status.setStyleSheet("color:#59f"); self.status.setMinimumWidth(1); self.stop_btns = []
        hb = QPushButton("? help"); hb.clicked.connect(lambda: Help(self).show())
        v.addWidget(row(QLabel("comfy"), self.url, self.status, 1, hb))
        self.tabs = QTabWidget(); v.addWidget(self.tabs, 1)
        self.build_base_tab(); self.build_wardrobe_tab(); self.build_tools_tab(); self.build_preview_tab()
        self.tabs.currentChanged.connect(lambda i: (i == 2 and self.tools_list()) or (i == 3 and self.refresh_preview()))
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self.base_go() if self.tabs.currentIndex() == 0 else self.go())
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.base_keep() if self.tabs.currentIndex() == 0 else (self.cut_cache and self.save_layer()))
        QShortcut(QKeySequence("Escape"), self, activated=self.stop.set)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=lambda: self.tabs.currentWidget() is self.tools_tab and self.tools_undo())

    def say(self, t): self.status.setText(t)

    def panel(self):
        """The right column: scrolls if it ever outgrows the window instead of pushing it off-screen. Its width is
        the splitter's to decide (drag the bar between the panes and the column); 500 px to start, 380 at least."""
        w = QWidget(); l = QVBoxLayout(w); l.setAlignment(Qt.AlignTop); l.setSpacing(6)
        sa = QScrollArea(); sa.setWidget(w); sa.setWidgetResizable(True); sa.setMinimumWidth(380)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); sa.setFrameShape(QScrollArea.NoFrame); return sa, l

    @staticmethod
    def split(left, right):
        """Panes left, column right, a draggable bar between them; the panes take the slack."""
        sp = QSplitter(Qt.Horizontal); sp.addWidget(left); sp.addWidget(right); sp.setCollapsible(0, False); sp.setCollapsible(1, False)
        sp.setStretchFactor(0, 1); sp.setStretchFactor(1, 0); sp.setSizes([1200, 500]); return sp

    def gobox(self, go_cb, refine_cb=None):
        """[steps][cfg] / [×seeds]   |  Go / Stop  — one block per tab; steps and cfg stay in sync across tabs.
        refine_cb adds a Refine button: the same generation, read from the picked result instead of the base."""
        w = QWidget(); g = QGridLayout(w); g.setContentsMargins(0, 0, 0, 0)
        steps = tip(QSpinBox(), "sampler steps — 25 is the 2.1 default; fewer = faster, rougher"); steps.setRange(1, 60); steps.setValue(25)
        cfg = tip(QSpinBox(), "guidance. 1 = the 2.1 default (negative prompt ignored). 2–4 makes the negative bite, costs double "
                              "time per step and can drift the look — an experiment, not a default"); cfg.setRange(1, 8); cfg.setValue(1)
        count = tip(QSpinBox(), "how many candidates per Go"); count.setRange(1, 16); count.setValue(4)
        go = big("Go", "#2a6", go_cb); go.setToolTip("Ctrl+Enter"); go.setMinimumHeight(44)
        stop = tip(QPushButton("Stop"), "Escape — interrupts Comfy after the current image"); stop.setEnabled(False); stop.clicked.connect(self.stop.set)
        self.stop_btns.append(stop)
        for c, (lab, wid) in enumerate((("steps", steps), ("cfg", cfg))): g.addWidget(QLabel(lab), 0, 2 * c); g.addWidget(wid, 0, 2 * c + 1)
        g.addWidget(QLabel("×seeds"), 1, 0); g.addWidget(count, 1, 1)
        g.addWidget(go, 0, 4); g.addWidget(stop, 1, 4); g.setColumnStretch(4, 1); g.setColumnMinimumWidth(4, 150)
        if refine_cb:
            self.refine_btn = tip(QPushButton("Refine picked"), "Generate from the PICKED result instead of the base: the prompt is the fix "
                                  "('remove the stray strand on the right', 'make the left shoe match the right'). With a zoomed scope only that "
                                  "region is redone, at hi-res; 'blanked' wipes it first so nothing wrong is copied. The cut still diffs against "
                                  "the base, so the layer stays just the garment. New candidates join the strip beside the old ones.")
            self.refine_btn.setStyleSheet("background:#a52; color:white; padding:6px"); self.refine_btn.setEnabled(False)
            self.refine_btn.clicked.connect(refine_cb); g.addWidget(self.refine_btn, 2, 4)
        return w, steps, cfg, count

    def refbox(self):
        rb = RefBox(self.pick_reference, lambda: self.set_reference(None, "")); self.refboxes.append(rb); return rb

    def pick_reference(self):
        p, _ = QFileDialog.getOpenFileName(self, "Reference image", self.args.out, "Images (*.png *.webp *.jpg *.jpeg)")
        if not p: return
        img = pil_open(p); img.thumbnail((1024, 1024))            # the model doesn't need more; keeps uploads and steps light
        self.set_reference(img, os.path.basename(p))

    def set_reference(self, img, name):
        self.ref_img, self.ref_name = img, name
        for rb in self.refboxes: rb.show(img, name)
        self.say(f"reference = {name}" if img is not None else "reference cleared")

    def extra_refs(self): return [self.ref_img] if self.ref_img is not None else []

    @staticmethod
    def link(a, b):
        a.valueChanged.connect(lambda v: b.value() != v and b.setValue(v)); b.valueChanged.connect(lambda v: a.value() != v and a.setValue(v))

    def generate(self, n, prompt, ref, size, done, res=0, negative="", steps=25, cfg=1, post=None, snap=True):
        if self.gen and self.gen.isRunning(): return self.say("busy — Stop first")
        self.stop.clear()
        for b in self.stop_btns: b.setEnabled(True)
        self.gen = Gen(Comfy(self.url.text()), self.tpl, n, prompt, ref, size, res, steps, self.stop, negative, cfg, post, self.extra_refs(), snap)
        self.gen.result.connect(done); self.gen.status.connect(self.say)
        self.gen.done.connect(lambda: [b.setEnabled(False) for b in self.stop_btns]); self.gen.start()

    # ---------- base tab ----------
    # One SOURCE image (left). Candidates land in the strip; the clicked one shows on the right; Use promotes it.
    #   edit this           → same size, the prompt is the change (neutral face, hair back, smile less)
    #   full body from this → 736×1216 canvas, the source is the reference (a face → a body)
    #   from text           → no image at all
    def build_base_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Base"); v = QVBoxLayout(tab)
        left = QWidget(); lv = QVBoxLayout(left)
        pair = QHBoxLayout(); lv.addLayout(pair, 1)
        lc, rc = QVBoxLayout(), QVBoxLayout(); pair.addLayout(lc, 1); pair.addLayout(rc, 1)
        self.worklab = QLabel("SOURCE — what Go reads: (none — Load source…, or Go from text)"); self.worklab.setStyleSheet("font-weight:bold"); self.worklab.setMinimumWidth(1)
        self.bpreview = Preview(); lc.addWidget(self.worklab); lc.addWidget(self.bpreview, 1)
        self.candlab = QLabel("SELECTED CANDIDATE — click a thumbnail"); self.candlab.setStyleSheet("font-weight:bold"); self.candlab.setMinimumWidth(1)
        self.cpreview = Preview(); rc.addWidget(self.candlab)
        # how to look at the candidate against the source: the views need the same size, so 'from this' and
        # 'from text' results (a new canvas) fall back to the candidate itself
        self.cview = QButtonGroup(self); vrow = QHBoxLayout(); vrow.setSpacing(6)
        for i, (label, mode, help_) in enumerate((
                ("candidate", "plain", "The result as it is."),
                ("changed", "changed", "What moved, over a dimmed copy: yellow = colour changed, red = became clear, cyan = became solid. "
                                       "The same map the fixer uses."),
                ("difference", "diff", "How much each pixel moved: faint yellow for a small shift, red for a big one, blue where only the "
                                       "alpha moved. Catches re-shading the eye misses side by side."),
                ("onion", "onion", "Half source, half candidate: what moved looks doubled, what stayed looks sharp."),
                ("alpha", "alpha", "The candidate's alpha as colour: 1–25 red, soft middle orange, 230–254 cyan, solid grey. Ghosts show."))):
            b = QRadioButton(label); b.mode = mode; b.setToolTip(help_); b.setChecked(i == 0); self.cview.addButton(b, i); vrow.addWidget(b)
        self.cblack = tip(QCheckBox("on black"), "The candidate (and the onion) over black instead of the checker: a dark halo or a stray "
                                                "light pixel shows, and so does what the model painted where she is clear.")
        self.cblack.toggled.connect(lambda *_: self.show_candidate())
        vrow.addStretch(1); vrow.addWidget(self.cblack); self.cview.buttonClicked.connect(lambda *_: self.show_candidate())
        # the view row sits UNDER the pane, so both images start at the same height and line up side by side
        rc.addWidget(self.cpreview, 1); rc.addLayout(vrow); self.bpreview.link(self.cpreview)
        self.bstrip = Strip(self.bselect); lv.addWidget(self.bstrip)            # under the panes only: the column keeps its full height

        right, r = self.panel(); v.addWidget(self.split(left, right), 1)
        self.bname = tip(QLineEdit("sapphire"), "Character id. Keep as base saves <name>-base.png into --out; point the cast `image` at it.")
        r.addWidget(row(QLabel("name"), self.bname))
        b1 = tip(QPushButton("Load source…"), "Any file becomes the source (her face)."); b1.clicked.connect(self.pick_work)
        b2 = tip(QPushButton("Save source…"), "Write the source anywhere as a PNG — a checkpoint you can Load again later."); b2.clicked.connect(self.save_work)
        r.addWidget(row(b1, b2, 1)); r.addWidget(self.refbox())
        self.bsnap = tip(QCheckBox("snap alpha"), "ON: every result, and anything loaded or saved here, gets its soft alpha snapped — ≤25 becomes clear "
                         "(and black), ≥230 solid. The model's own alpha is soft everywhere, so this is normally right.\n"
                         "OFF: alpha is left exactly as it comes. Try it when an edit goes dark or see-through in places it should not.\n"
                         "'region: detail' never snaps — only the colour of that result is used, its alpha is thrown away.")
        self.bsnap.setChecked(True); r.addWidget(self.bsnap)
        self.bmode = QButtonGroup(self); mrow = QHBoxLayout()
        modes = (("edit this (same size)", "edit", "The source's own pixels are the starting canvas, at its own size. The prompt is the change."),
                 (f"new {FRAME[0]}×{FRAME[1]} from this", "body", f"An EMPTY {FRAME[0]}×{FRAME[1]} canvas; the source is wired in as a reference image the model "
                                                                  "looks at (a face becomes a body). Different canvas from 'edit this', not a different prompt."),
                 (f"new {FRAME[0]}×{FRAME[1]} from text", "text", "An empty canvas and the prompt. The source is not shown to the model."),
                 ("region: detail", "region", "Zoom the SOURCE pane onto a region; it's cut out, upscaled to ~1024 px, edited, scaled back. Her outline "
                                              "never changes and her edge pixels keep their colour — eyes, skin, strand texture, colour."),
                 ("region: add/remove", "region_add", "Same crop, but alpha AND colour inside the box come from the model. Hair grows, strands go — "
                                                      "when it honours the transparent background. When it invents a backdrop you'll see that colour "
                                                      "at the edges: another seed, or 'edit this'."),
                 ("region: replace", "region_blank", "Zoom onto the part that is WRONG. It is wiped to transparent before the model sees it, so there is "
                                                     "nothing wrong left to copy — the prompt says what belongs there, the model rebuilds it from the "
                                                     "surroundings (it sees the hole plus half its size around). Only the hole is pasted back."))
        grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0)
        for i, (t, m, tt) in enumerate(modes):
            rb = tip(QRadioButton(t), tt); rb.mode = m; rb.setChecked(i == 0); self.bmode.addButton(rb, i); grid.addWidget(rb, i // 2, i % 2)
        r.addLayout(grid); self.bmode.buttonClicked.connect(self.mode_changed)
        r.addWidget(QLabel("prompt"))
        self.bprompt = tip(QPlainTextEdit(EDIT_HINT), "Body/text prompts keep the RGBA sentences at both ends — that's what makes the background "
                                                      "transparent. The prefill only swaps with the mode while you haven't edited it.")
        self.bprompt.setFixedHeight(130); r.addWidget(self.bprompt)
        self.bneg = tip(QLineEdit(), "negative prompt — ignored at cfg 1 (see the cfg box at the top)"); self.bneg.setPlaceholderText("negative (needs cfg > 1)")
        r.addWidget(self.bneg)
        gb, self.bsteps, self.bcfg, self.bcount = self.gobox(self.base_go); r.addWidget(gb)
        self.buse = big("◀ Use selected as source", "#a52", self.use_selected); self.buse.setEnabled(False); r.addWidget(self.buse)
        self.buse.setToolTip("The selected candidate becomes the source: the next Go reads IT. Loop: Go → click → Use → change the prompt → Go …")
        self.bkeep = big("Keep as base →", "#36c", self.base_keep); self.bkeep.setEnabled(False); r.addWidget(self.bkeep)
        self.bkeep.setToolTip("Ctrl+S. The SOURCE becomes <name>-base.png and the Wardrobe tab opens on it.")

    def mode(self): return self.bmode.checkedButton().mode

    def mode_changed(self, *_):
        if self.bprompt.toPlainText().strip() in self.DEFAULTS.values(): self.bprompt.setPlainText(self.DEFAULTS[self.mode()])

    def set_work(self, img, name):
        self.work, self.work_name = img, name
        self.worklab.setText(f"SOURCE — what Go reads: {name}  {img.size[0]}×{img.size[1]}")
        self.bpreview.set_image(img); self.bkeep.setEnabled(True)

    def snapped(self, img):
        """The alpha snap, unless the Base tab's switch is off."""
        return clean_alpha(img) if self.bsnap.isChecked() else img

    def pick_work(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load source", self.args.out, "Images (*.png *.webp *.jpg *.jpeg)")
        if p: self.set_work(self.snapped(pil_open(p)), os.path.basename(p))

    def save_work(self):
        if self.work is None: return QMessageBox.information(self, "save", "nothing to save yet")
        start = os.path.join(self.side_dir(), f"{self.bname.text().strip() or 'work'}-work.png")
        p, _ = QFileDialog.getSaveFileName(self, "Save source", start, "PNG (*.png)")
        if not p: return
        if not p.lower().endswith(".png"): p += ".png"
        self.snapped(self.work).save(p); self.say(f"saved → {p}")

    def base_go(self):
        prompt = self.bprompt.toPlainText().strip()
        if not prompt: return QMessageBox.information(self, "go", "write a prompt")
        mode = self.mode(); ref, size, res = None, FRAME, 0
        if mode != "text" and self.work is None: return QMessageBox.information(self, "go", "no source — Load source…, or click a candidate")
        post = None
        if mode == "edit": ref, size = self.work, None
        elif mode == "body": ref, res = self.work, 1024          # the face is conditioning, not the canvas
        elif mode in ("region", "region_add"):
            box = self.bpreview.visible_box()
            if not box: return QMessageBox.information(self, "go", "zoom the SOURCE pane onto the region first (wheel), then Go")
            ref, pad = crop_up(self.work, box); size = None; src = self.work; ma = mode == "region_add"
            post = lambda img, src=src, box=box, pad=pad, ma=ma: paste_down(src, img, box, pad, model_alpha=ma)
            self.say(f"region {box[2] - box[0]}×{box[3] - box[1]} → {ref.size[0]}×{ref.size[1]} for the model")
        elif mode == "region_blank":
            hole = self.bpreview.visible_box()
            if not hole: return QMessageBox.information(self, "go", "zoom the SOURCE pane onto the part to replace first (wheel), then Go")
            ref, pad, big, wiped = blank_hole(self.work, hole); size = None
            post = lambda img, src=self.work, wiped=wiped, hole=hole, big=big, pad=pad: paste_hole(src, wiped, img, hole, big, pad)
            self.say(f"replacing {hole[2] - hole[0]}×{hole[3] - hole[1]}; the model sees {big[2] - big[0]}×{big[3] - big[1]} → {ref.size[0]}×{ref.size[1]}")
        self.bresults = []; self.bsel = None; self.bstrip.set([], None); self.buse.setEnabled(False)
        self.cpreview.clear_image(); self.candlab.setText("SELECTED CANDIDATE — click a thumbnail")
        # region detail keeps only the result's colour: snapping its alpha painted the low-alpha middle black,
        # and the paste's edge ramp faded exactly that into the centre (found 2026-10-01)
        self.generate(self.bcount.value(), prompt, ref, size, self.base_done, res, self.bneg.text().strip(), self.bsteps.value(), self.bcfg.value(), post,
                      snap=self.bsnap.isChecked() and mode != "region")

    def base_done(self, seed, img):
        self.bresults.append((seed, img)); self.bstrip.set(self.bresults, self.bsel)

    def bselect(self, i):
        self.bsel = i; seed, img = self.bresults[i]
        self.candlab.setText(f"SELECTED CANDIDATE — seed {seed}  {img.size[0]}×{img.size[1]}"); self.show_candidate(); self.buse.setEnabled(True)

    def show_candidate(self):
        """The selected candidate in the right pane, in the view picked under its label."""
        if self.bsel is None: return
        img = self.bresults[self.bsel][1]; mode = self.cview.checkedButton().mode
        same = self.work is not None and self.work.size == img.size
        if mode == "alpha": shown = alpha_heat(img)
        elif mode == "changed" and same: shown = change_map(self.work, img, (24, 24, 28))
        elif mode == "diff" and same: shown = diff_view(self.work, img)
        elif mode == "onion" and same: shown = onion(self.work, img)
        else:
            shown = img
            if mode != "plain": self.say("that view needs a source of the same size — showing the candidate itself")
        if self.cblack.isChecked() and mode in ("plain", "onion"): shown = on_bg(shown, (0, 0, 0))
        self.cpreview.set_image(shown)

    def use_selected(self):
        if self.bsel is None: return
        seed, img = self.bresults[self.bsel]; self.set_work(img, f"candidate seed {seed}"); self.say(f"source = candidate seed {seed} — Go now reads it")

    def base_keep(self):
        if self.work is None: return
        name = self.bname.text().strip() or "base"; os.makedirs(self.args.out, exist_ok=True)
        out = os.path.join(self.args.out, f"{name}-base.png"); self.snapped(self.work).save(out)
        json.dump({"name": name, "from": self.work_name, "mode": self.mode(), "steps": self.bsteps.value(), "cfg": self.bcfg.value(),
                   "negative": self.bneg.text().strip(), "reference": self.ref_name, "prompt": self.bprompt.toPlainText().strip()},
                  open(os.path.join(self.side_dir(), f"{name}-base.json"), "w"), indent=1)
        self.load_base(out); self.say(f"base saved → {out}")

    def pick_base(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load base", self.args.out, "Images (*.png *.webp)")
        if p: self.load_base(p)

    def load_base(self, path):
        self.base = pil_open(path); self.base_path = path; soft = alpha_is_soft(self.base)
        if soft: self.base = clean_alpha(self.base)
        if os.path.basename(path).endswith("-base.png"): self.bname.setText(os.path.basename(path)[:-9])
        self.set_work(self.base, os.path.basename(path))
        self.gens = {}; self.results = []; self.sel = None; self.cut_cache = None; self._gb = None
        self.rpreview.clear_image(); self.refresh_context(); self.show_base(); self.build_strip()
        self.say(f"base {os.path.basename(path)} {self.base.size[0]}×{self.base.size[1]}" + ("  — its alpha was soft: cleaned in memory; Keep as base to save it clean" if soft else ""))
        self.tabs.setCurrentIndex(1)

    # ---------- wardrobe tab ----------
    def build_wardrobe_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Wardrobe"); v = QVBoxLayout(tab)
        left = QWidget(); lv = QVBoxLayout(left)
        lv.addWidget(hint("LEFT: exactly what the model sees — the base plus whatever you check under 'wear while generating'. "
                          "RIGHT: the picked result in the chosen cut view; 'over base' is the plain base + this item, 'worn' adds what it was made over. "
                          "Wheel = zoom, drag = pan, double-click = fit; both panes move together."))
        pair = QHBoxLayout(); lv.addLayout(pair, 1); lc, rc = QVBoxLayout(), QVBoxLayout(); pair.addLayout(lc, 1); pair.addLayout(rc, 1)
        self.lpreview = Preview(); self.rpreview = Preview(); lc.addWidget(self.lpreview, 1); rc.addWidget(self.rpreview, 1); self.lpreview.link(self.rpreview)
        # how to look at the picked result: a row UNDER the right pane, like the Base tab's candidate views
        self.view = QButtonGroup(self); vr = QHBoxLayout(); vr.setSpacing(6)
        for i, (t, tt) in enumerate((("result", "what the model made"), ("layer", "the cut-out alone, on a checker"),
                                     ("on dark", "the cut-out alone on a flat dark background — re-drawn skin that rode into the layer shows here"),
                                     ("over base", "the plain base + this item only: anything of the worn layers that rode into this one shows here"),
                                     ("worn", "her wearing what this result was made over, plus this item — as the card will show it"))):
            rb = tip(QRadioButton(t), tt); rb.setChecked(i == 4); self.view.addButton(rb, i); vr.addWidget(rb)
        vr.addStretch(1); rc.addLayout(vr); self.view.buttonClicked.connect(lambda *_: self.show_cut())
        self.wstrip = Strip(self.select); lv.addWidget(self.wstrip)

        right, r = self.panel(); v.addWidget(self.split(left, right), 1)
        lb = QPushButton("Load base…"); lb.clicked.connect(self.pick_base)
        self.item = tip(QComboBox(), "Type ANY name — space_suit, red_scarf, face_smile, hair_front_long, hair_back_long (underscores; a slot prefix picks the "
                                     "slot). The dropdown lists what's already in --out. Saves as layer-<item>.png; wire it as a start item in the game's gear editor.")
        self.item.setEditable(True); self.item.addItems([NONE] + sorted(self.item_slot)); self.item.setCurrentText(NONE); self.item.currentTextChanged.connect(self.item_changed)
        self.slot = tip(QComboBox(), "Sets the cut's shape and the stack order. face = a head patch (expressions). hair_back = drawn UNDER her "
                                     "(behind her body and every garment); hair_front = over every garment, under the hat. Bottom→top: hair_back, face, "
                                     "underwear, bra, socks, pants, shirt, shoes, body, jacket, outer, hair_front, hat, in_hand; pack and holster on the side.")
        self.slot.addItems(SLOTS); self.slot.setCurrentText("shirt"); self.slot.currentTextChanged.connect(self.slot_changed)
        r.addWidget(row(lb, QLabel("item"), self.item, QLabel("slot"), self.slot)); r.addWidget(self.refbox())
        ctxlab = tip(QLabel("wear while generating"), "Stack saved layers under the NEXT generation — wear the top while making the blazer, so colours "
                                                       "can match. The LEFT pane is exactly what the model will see; the new layer still holds only the new garment. "
                                                       "A result remembers what it was made over and is always cut against that, whatever you check later. "
                                                       "(Checking the item you're remaking shows the model the old one — leave that off.)")
        r.addWidget(ctxlab); self.ctx = QWidget(); self.ctx_lay = QGridLayout(self.ctx); self.ctx_lay.setContentsMargins(0, 0, 0, 0); r.addWidget(self.ctx)
        self.prompt = tip(QPlainTextEdit(GARMENT_HINT), "The CHANGE only. Don't describe her — she is the reference. Follows the item name until you edit it.")
        self.prompt.setFixedHeight(76); r.addWidget(self.prompt)
        self.neg = tip(QLineEdit(), "negative prompt — ignored at cfg 1 (see the cfg box at the top)"); self.neg.setPlaceholderText("negative (needs cfg > 1)")
        r.addWidget(self.neg)
        self.wscope = QButtonGroup(self); sr = QGridLayout(); sr.setContentsMargins(0, 0, 0, 0); sr.addWidget(QLabel("generate:"), 0, 0)
        for i, (t, tt) in enumerate((("whole image", "the whole base goes to the model — zoom is only your view"),
                                     ("zoomed region (hi-res)", "only the LEFT pane's visible rectangle goes to the model, upscaled to ~1024 px, then pasted back "
                                                                "and cut as usual. For small items — shoes, a belt, earrings — the model spends its whole budget on them."),
                                     ("zoomed, blanked (replace)", "the LEFT pane's visible rectangle is WIPED to transparent before the model sees it, so wrong feet "
                                                                   "or a bad sleeve can't be copied; the prompt says what goes there and the model rebuilds it from "
                                                                   "the surroundings. Only the hole comes back. Works from the base (Go) or the picked result (Refine)."))):
            rb = tip(QRadioButton(t), tt); rb.setChecked(i == 0); self.wscope.addButton(rb, i); sr.addWidget(rb, (i + 1) // 2, (i + 1) % 2)
        r.addLayout(sr)
        gb, self.steps, self.cfg, self.count = self.gobox(self.go, lambda: self.go(refine=True)); r.addWidget(gb)
        self.link(self.steps, self.bsteps); self.link(self.cfg, self.bcfg)

        c = QGroupBox("Cut"); cl = QVBoxLayout(c); cl.setSpacing(4); r.addWidget(c)
        self.thresh = self.slider(cl, "threshold", 4, 80, 24, "how different a pixel must be to count as garment. Raise it (~64) if the model re-shaded skin next to the garment.")
        self.blob = self.slider(cl, "min blob", 0, 5000, 400, "islands smaller than this many px are dropped")
        self.trim = tip(QDoubleSpinBox(), "shave the garment's FREE edges inward by this many px (half-px steps) — the last sliver of re-drawn skin "
                                          "where the garment ends. Never touches the edge along her outline.")
        self.trim.setRange(0, 6); self.trim.setSingleStep(0.5); self.trim.setDecimals(1); self.trim.valueChanged.connect(lambda *_: self.schedule_cut())
        self.reach = tip(QSpinBox(), "hug her outline: garment within this many px of her edge is extended to it, and that outer band takes the "
                                     "garment's colour from further in (the model paints a lighter, skin-blended rim along her edge). 0 = as the model drew it.")
        self.reach.setRange(0, 4); self.reach.setValue(2); self.reach.valueChanged.connect(lambda *_: self.schedule_cut())
        cl.addWidget(row(QLabel("trim edge px"), self.trim, QLabel("reach her edge px"), self.reach, 1))
        self.pieces = tip(QSpinBox(), "keep only the N largest blobs — 1 for pants or a shirt, 2 for shoes or socks, 0 = all. The model re-shades "
                                      "skin in small islands elsewhere; a garment is one or two pieces.")
        self.pieces.setRange(0, 6); self.pieces.setValue(1); self.pieces.valueChanged.connect(lambda *_: self.schedule_cut())
        self.outside = tip(QCheckBox("may stick out"), "OFF: the layer can only exist inside the base's own alpha — the model's edge halo never gets in. "
                                                       "ON (coats, hats, bags, hair): new pixels outside her count too, and that outer edge is shaved a few px.")
        self.outside.toggled.connect(lambda *_: self.schedule_cut())
        self.skin = tip(QCheckBox("ignore skin re-shading"), "Drops changed pixels that are HER skin colour (sampled from her face) both before and after — "
                                                              "the model re-lighting her, re-tinting her legs beside the leggings, shading her jaw under new hair. "
                                                              "Hair over hair passes. Turn OFF for a garment that is itself skin-coloured (beige pants, a tan top).")
        self.skin.setChecked(True); self.skin.toggled.connect(lambda *_: self.schedule_cut())
        cl.addWidget(row(QLabel("pieces"), self.pieces, self.outside, self.skin, 1))
        self.keyon = tip(QCheckBox("key colour"), "CHROMA KEY instead of the diff: the layer is every pixel of this hue that wasn't already that hue in what "
                                                  "the model saw. Generate the garment in a colour she can't be ('bright blue hair'), cut by key, then turn it "
                                                  "brown on the Tools tab (recolour → to colour…). Making a second layer over a worn keyed one? Give it a "
                                                  "different key (green over blue). Threshold and the skin guard don't apply.")
        self.keyon.toggled.connect(lambda *_: self.schedule_cut()); self.key = (40, 80, 255)
        self.keybtn = tip(QPushButton(""), "the key colour — click to pick"); self.keybtn.setFixedWidth(44); self.keybtn.clicked.connect(self.pick_key); self.show_key()
        self.keytol = tip(QSpinBox(), "how far a hue may stray from the key, in degrees"); self.keytol.setRange(5, 90); self.keytol.setValue(35)
        self.keytol.valueChanged.connect(lambda *_: self.schedule_cut())
        cl.addWidget(row(self.keyon, self.keybtn, QLabel("± °"), self.keytol, 1))
        self.save_btn = big("Save layer", "#36c", self.save_layer); self.save_btn.setEnabled(False)
        self.del_btn = tip(QPushButton("Delete"), "Remove this item's files from --out: the layer, icon, preview, raw result and recipe. Asks first.")
        self.del_btn.setStyleSheet("background:#733; color:white; padding:8px"); self.del_btn.clicked.connect(self.delete_item); self.del_btn.setEnabled(False)
        cl.addWidget(row(self.save_btn, self.del_btn))
        self.save_btn.setToolTip("Ctrl+S. Writes <out>/layer-<item>.png, icon-<item>.webp for the card's slot tile, "
                                 "a browsable <out>/preview/<item>.png, and the recipe + raw result in <out>/wardrobe/.")

    # ---------- tools tab ----------
    # Trim a SOURCE image's opacity and edges, and SEE it: top row = the real image on a flat colour (before | after),
    # bottom row = a change map of exactly what will change. Three memory slots, Apply as source, Save.
    # ---------- preview tab ----------
    # A viewer: her in the middle, every saved layer as a checkbox by slot, a few ways to look, Export as PNG.
    def build_preview_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Preview"); v = QVBoxLayout(tab)
        self.ppreview = Preview()
        right, r = self.panel(); v.addWidget(self.split(self.ppreview, right), 1)
        self.pbase = tip(QCheckBox("her (the base)"), "Off: only the checked layers, over the background — clothing alone."); self.pbase.setChecked(True)
        self.pbg = tip(QComboBox(), "What the composite sits on. 'none' is transparent: the checker here, nothing in the export.")
        self.pbg.addItems(["none", "black", "white", "grey"])
        self.pview = tip(QComboBox(), "image: as it would show on the card. alpha: the composite's alpha as colour (ghosts, soft edges). "
                                      "coverage: what the checked layers change against her, yellow = colour, cyan = new solid pixels.")
        self.pview.addItems(["image", "alpha", "coverage"])
        for w in (self.pbase, self.pbg, self.pview): (w.toggled if isinstance(w, QCheckBox) else w.currentIndexChanged).connect(lambda *_: self.show_preview())
        r.addWidget(row(self.pbase, QLabel("on"), self.pbg, QLabel("view"), self.pview))
        self.pinfo = hint("(no layers yet)"); r.addWidget(self.pinfo)
        self.pboxes = {}; box = QWidget(); self.play = QVBoxLayout(box); self.play.setAlignment(Qt.AlignTop); self.play.setSpacing(2); r.addWidget(box)
        b_off = QPushButton("all off"); b_off.clicked.connect(lambda: [b.setChecked(False) for b in self.pboxes.values()])
        b_ref = tip(QPushButton("refresh"), "Read --out again: a layer saved on the Wardrobe tab since you came here."); b_ref.clicked.connect(self.refresh_preview)
        b_exp = big("Export PNG…", "#36c", self.export_preview); b_exp.setToolTip("The composite exactly as shown, as a PNG. 'none' keeps it transparent.")
        r.addWidget(row(b_off, b_ref)); r.addWidget(b_exp)

    def refresh_preview(self):
        """Every saved layer in --out, one checkbox each, grouped by slot in the card's order. Ticks survive a refresh."""
        old = {k: b.isChecked() for k, b in self.pboxes.items()}; self.pboxes = {}
        while self.play.count():
            w = self.play.takeAt(0).widget()
            if w: w.deleteLater()
        files = self.layer_files()
        for it in files:
            if it not in self.item_slot and self.recipe(it).get("slot"): self.item_slot[it] = self.recipe(it)["slot"]
        groups = {}
        for it in files: groups.setdefault(self.item_slot.get(it) or slot_for(it) or "other", []).append(it)
        for sl in [s for s in LAYER_ORDER + ["pack", "holster", "other"] if s in groups]:
            lab = QLabel(sl); lab.setStyleSheet("color:#8a8a8a; font-size:11px; margin-top:6px"); self.play.addWidget(lab)
            for it in sorted(groups[sl]):
                b = QCheckBox(it); b.setChecked(old.get(it, False))
                b.toggled.connect(lambda *_: self.show_preview()); self.pboxes[it] = b; self.play.addWidget(b)
        if not files: self.play.addWidget(hint("(no layers saved in --out yet — Save layer on the Wardrobe tab)"))
        self.show_preview()

    def preview_image(self):
        """Her with the checked layers worn as the card draws them (hair_back under her). None when there is nothing."""
        files = self.layer_files(); on = [it for it, b in self.pboxes.items() if b.isChecked() and it in files]
        base = self.base if self.pbase.isChecked() else None
        if base is None and not on: return None
        if base is None and self.base is not None: base = Image.new("RGBA", self.base.size, (0, 0, 0, 0))   # keep her size
        return stack(base, [(it, pil_open(files[it])) for it in on], self.item_slot)

    def preview_shown(self):
        """The composite in the picked view, on the picked background."""
        img = self.preview_image()
        if img is None: return None
        view, bg = self.pview.currentText(), {"black": (0, 0, 0), "white": (255, 255, 255), "grey": (128, 128, 128)}.get(self.pbg.currentText())
        if view == "alpha": return alpha_heat(img)
        if view == "coverage" and self.base is not None: return change_map(self.base, img, bg or (24, 24, 28))
        return on_bg(img, bg) if bg else img

    def show_preview(self):
        shown = self.preview_shown(); self.ppreview.set_image(shown)
        on = sum(1 for b in self.pboxes.values() if b.isChecked())
        self.pinfo.setText(f"{len(self.pboxes)} layers in --out, {on} on" + (f" · {shown.size[0]}×{shown.size[1]}" if shown is not None else "")
                           + ("" if self.base is not None else " · no base: --base, or Keep as base on the Base tab"))

    def export_preview(self):
        img = self.preview_shown()
        if img is None: return QMessageBox.information(self, "export", "nothing to export — tick a layer, or load a base")
        on = [it for it, b in self.pboxes.items() if b.isChecked()]
        name = (self.bname.text().strip() or "preview") + ("-" + "-".join(on[:3]) if on else "") + (f"-and-{len(on) - 3}-more" if len(on) > 3 else "")
        p, _ = QFileDialog.getSaveFileName(self, "Export composite", os.path.join(self.args.out, name + ".png"), "PNG (*.png)")
        if not p: return
        if not p.lower().endswith(".png"): p += ".png"
        img.save(p); self.say(f"exported → {p}")

    def build_tools_tab(self):
        tab = QWidget(); self.tabs.addTab(tab, "Tools"); v = QVBoxLayout(tab); self.tools_tab = tab
        left = QWidget(); lv = QVBoxLayout(left)
        grid = QGridLayout(); lv.addLayout(grid, 1)
        self.tviews = [PaintView(self.tools_dab, lambda: self.tools_timer.start())] + [Preview() for _ in range(3)]
        self.tcaps = []
        for i, (lab, pv) in enumerate(zip(("BEFORE · on flat colour", "AFTER · on flat colour", "CHANGE MAP · red → empty, cyan → solid, yellow recoloured", "AFTER · over checker"), self.tviews)):
            l = QLabel(lab); l.setStyleSheet("font-weight:bold"); l.setWordWrap(True); l.setMinimumWidth(1)   # never set the column's minimum width
            grid.addWidget(l, (i // 2) * 2, i % 2); grid.addWidget(pv, (i // 2) * 2 + 1, i % 2); self.tcaps.append(l)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        for i in range(4):
            for j in range(i + 1, 4): self.tviews[i].link(self.tviews[j])
        grid.setRowStretch(1, 1); grid.setRowStretch(3, 1)
        self.thist = QLabel(""); self.thist.setStyleSheet("font-family:monospace; color:#9ad"); self.thist.setWordWrap(True); self.thist.setMinimumWidth(1); lv.addWidget(self.thist)

        right, r = self.panel(); v.addWidget(self.split(left, right), 1)
        self.tslotf = tip(QComboBox(), "narrow the item list to one slot"); self.tslotf.addItems(["source image"] + ["all layers"] + SLOTS)
        self.titem = tip(QComboBox(), "which saved layer to fix (loaded from --out). 'source image' = the Base tab's source.")
        self.tslotf.currentTextChanged.connect(lambda *_: self.tools_list()); self.titem.currentTextChanged.connect(lambda *_: self.tools_refresh())
        r.addWidget(row(QLabel("fix"), self.tslotf, self.titem))
        self.tbg = tip(QComboBox(), "flat colour behind the top-left pane — dark shows a white border, white shows a dark halo"); self.tbg.addItems(list(FLAT_BGS))
        self.tbg.currentTextChanged.connect(lambda *_: self.tools_timer.start()); r.addWidget(row(QLabel("flat colour"), self.tbg))
        g = QGroupBox("eraser — by hand, BEFORE the trims below"); gl = QVBoxLayout(g); gl.setSpacing(4); r.addWidget(g)
        self.tbrush = tip(QCheckBox("brush on (top-left pane)"), "LEFT button erases, RIGHT restores what you erased, MIDDLE drags, wheel zooms. Hard edge. "
                                                                  "The red ring is the brush at the image's scale. Ctrl+Z undoes the last stroke.")
        self.tbrush.toggled.connect(lambda on: self.tviews[0].set_brush(on))
        self.tsize = tip(QSpinBox(), "brush diameter in image pixels"); self.tsize.setRange(1, 128); self.tsize.setValue(8)
        self.tsize.valueChanged.connect(lambda v: setattr(self.tviews[0], "radius", v / 2))
        tu = tip(QPushButton("undo"), "Ctrl+Z — the last stroke"); tu.clicked.connect(self.tools_undo)
        tc = tip(QPushButton("clear"), "forget all erasing on this image (undoable)"); tc.clicked.connect(self.tools_clear)
        gl.addWidget(row(self.tbrush, 1)); gl.addWidget(row(QLabel("size px"), self.tsize, tu, tc, 1))
        g = QGroupBox("trim opacity"); gl = QVBoxLayout(g); gl.setSpacing(4); r.addWidget(g)
        self.t_lo = self.tslider(gl, "empty below", 0, 128, 25, "alpha at or below this → 0. Kills the low-opacity junk (squares) that isn't her.")
        self.t_hi = self.tslider(gl, "solid above", 128, 255, 230, "alpha at or above this → 255. Makes a slightly see-through body solid.")
        self.t_min = self.tslider(gl, "drop islands px", 0, 3000, 0, "floating alpha blobs smaller than this many px are removed")
        g = QGroupBox("recolour — hue, strength and lightness of every pixel; alpha untouched"); gl = QVBoxLayout(g); gl.setSpacing(4); r.addWidget(g)
        self.t_hue = self.tslider(gl, "hue °", -180, 180, 0, "rotate every colour's hue — blue hair to brown is about +120")
        self.t_sat = self.tslider(gl, "colour %", 0, 300, 100, "scale how colourful it is — 0 = grey, 100 = as is")
        self.t_light = self.tslider(gl, "lightness", -100, 100, 0, "darker or lighter, same shading")
        tcol = tip(QPushButton("to colour…"), "pick the colour the garment should be: sets the three sliders so its dominant colour lands there")
        tcol.clicked.connect(self.tools_to_colour); gl.addWidget(row(tcol, 1))
        g = QGroupBox("trim edges"); gl = QVBoxLayout(g); gl.setSpacing(4); r.addWidget(g)
        self.t_trim = self.tslider(gl, "trim outline px", 0, 12, 0, "erode the outline inward by this many px — eats a white border baked into an edge, shrinks a garment that overshoots")
        self.t_grow = self.tslider(gl, "grow outline px", 0, 12, 0, "dilate the outline outward by this many px, grown pixels take the nearest colour — a garment that stops a hair short of her edge")
        self.t_fringe = self.tslider(gl, "defringe px", 0, 12, 0, "edge pixels take the colour of the nearest solid pixels within this radius — a border tint without losing the edge")
        mem = QGridLayout(); mem.setContentsMargins(0, 0, 0, 0); self.tslots = {}
        for i, k in enumerate("ABC"):
            b1 = tip(QPushButton(f"store {k}"), "remember the current AFTER image + settings in this slot"); b1.clicked.connect(lambda _=False, k=k: self.tools_store(k))
            b2 = tip(QPushButton(f"recall {k}"), "bring this slot's settings back (and show its image)"); b2.clicked.connect(lambda _=False, k=k: self.tools_recall(k)); b2.setEnabled(False)
            self.tslots[k] = (None, None, b2); mem.addWidget(b1, 0, i); mem.addWidget(b2, 1, i)
        r.addLayout(mem)
        r.addWidget(big("◀ Apply", "#a52", self.tools_apply)); r.addWidget(hint("source image: the AFTER image becomes the Base tab's source (then Keep as base). "
                                                                                  "layer: layer-<item>.png is rewritten on disk, the original kept once in wardrobe/."))
        bs = QPushButton("Save AFTER as…"); bs.clicked.connect(self.tools_save); r.addWidget(bs)
        self.tools_timer = QTimer(self); self.tools_timer.setSingleShot(True); self.tools_timer.setInterval(150); self.tools_timer.timeout.connect(self.tools_refresh)
        self.tools_result = None; self.tools_item = None; self.tcache = (None, None, None); self.tundo = []

    def tslider(self, lay, label, lo, hi, val, tt):
        s = tip(QSlider(Qt.Horizontal), tt); s.setRange(lo, hi); s.setValue(val); num = QLabel(str(val)); num.setFixedWidth(44)
        s.valueChanged.connect(lambda v: (num.setText(str(v)), self.tools_timer.start()))
        lay.addWidget(row(tip(QLabel(label), tt), s, num)); return s

    def tools_settings(self):
        return dict(lo=self.t_lo.value(), hi=max(self.t_hi.value(), self.t_lo.value() + 1), trim_px=self.t_trim.value(), defringe_px=self.t_fringe.value(),
                    min_px=self.t_min.value(), grow_px=self.t_grow.value())

    def tools_colour(self):
        return dict(hue=float(self.t_hue.value()), sat=self.t_sat.value() / 100, light=float(self.t_light.value()))

    def tools_to_colour(self):
        src, _, _ = self.tools_input()
        if src is None: return
        c = QColorDialog.getColor(QColor(110, 70, 40), self, "the colour it should be")
        if not c.isValid(): return
        sh = colour_shift(src, (c.red(), c.green(), c.blue()))
        if sh is None: return self.say("nothing in it has a hue to shift")
        for w, v in ((self.t_hue, sh[0]), (self.t_sat, sh[1] * 100), (self.t_light, sh[2])): w.setValue(int(round(v)))

    def pick_key(self):
        c = QColorDialog.getColor(QColor(*self.key), self, "key colour")
        if c.isValid(): self.key = (c.red(), c.green(), c.blue()); self.show_key(); self.schedule_cut()

    def show_key(self):
        self.keybtn.setStyleSheet("background: rgb(%d, %d, %d); border: 1px solid #888" % self.key)

    def tools_list(self):
        """Fill the item box from the layers on disk, filtered by the slot box. 'source image' = the Base tab's source."""
        f = self.tslotf.currentText(); files = self.layer_files()
        for it in files:
            if it not in self.item_slot and self.recipe(it).get("slot"): self.item_slot[it] = self.recipe(it)["slot"]
        items = [] if f == "source image" else sorted(it for it in files if f == "all layers" or self.item_slot.get(it) == f)
        cur = self.titem.currentText(); self.titem.blockSignals(True); self.titem.clear()
        self.titem.addItems(items or ["(source image)"] if f == "source image" else items or ["(no layers in this slot)"])
        if cur in items: self.titem.setCurrentText(cur)
        self.titem.blockSignals(False); self.tools_refresh()

    def tools_input(self):
        """(image, kind, item) — the source, or a saved layer loaded from disk — with your erasing applied. The
        pristine image and its erase mask live in tcache until the item (or the source) changes."""
        if self.tslotf.currentText() == "source image": kind, it, key, load = "source", None, ("source", id(self.work)), (lambda: self.work)
        else:
            name = self.titem.currentText(); f = self.layer_files().get(name)
            kind, it, key, load = "layer", (name if f else None), ("layer", name), (lambda: pil_open(f) if f else None)
        if self.tcache[0] != key:
            img = load(); self.tcache = (key, img, np.zeros(img.size[::-1], bool) if img is not None else None); self.tundo = []
        _, img, erased = self.tcache
        if img is not None and erased.any():
            A = np.asarray(img).copy(); A[erased] = 0; img = Image.fromarray(A)
        return img, kind, it

    def tools_dab(self, x0, y0, x1, y1, erase, first):
        """One brush step on the Tools input: a hard disc, joined to the last point. Left = erase, right = restore."""
        key, img, erased = self.tcache
        if img is None: return
        if first: self.tundo = (self.tundo + [erased.copy()])[-40:]
        m = erased.view(np.uint8); r = max(0, self.tsize.value() // 2); v = 1 if erase else 0
        cv2.line(m, (int(x0), int(y0)), (int(x1), int(y1)), v, thickness=max(1, 2 * r)); cv2.circle(m, (int(x1), int(y1)), r, v, -1)
        src, kind, _ = self.tools_input()                                   # the top-left pane follows the brush live; the fixers run on release
        self.tviews[0].set_image(on_bg(src, FLAT_BGS[self.tbg.currentText()]))

    def tools_undo(self):
        if not self.tundo: return
        key, img, _ = self.tcache; self.tcache = (key, img, self.tundo.pop()); self.tools_refresh()

    def tools_clear(self):
        key, img, erased = self.tcache
        if img is None or not erased.any(): return
        self.tundo = (self.tundo + [erased.copy()])[-40:]; self.tcache = (key, img, np.zeros_like(erased)); self.tools_refresh()

    def tools_refresh(self):
        src, kind, it = self.tools_input(); self.tools_item = it
        if src is None:
            for pv in self.tviews: pv.clear_image(); self.tools_result = None
            self.thist.setText("no source — Load source… on the Base tab" if kind == "source" else "no layer — pick a slot and an item (saved layers in --out)"); return
        st = self.tools_settings(); col = self.tools_colour()
        if col != dict(hue=0.0, sat=1.0, light=0.0): src = recolour(src, **col)                    # colour first: the defringe reads colours
        self.tools_result = fix_alpha(src, **st); bg = FLAT_BGS[self.tbg.currentText()]
        if kind == "layer" and self.base is not None:
            caps = ("LAYER alone · on flat colour (added skin shows here)", "AFTER · over DIMMED base (gaps and overshoot at her edge)", "CHANGE MAP · red → empty, cyan → solid, yellow recoloured", "AFTER · over base")
            imgs = (on_bg(src, bg), over_dim(self.base, self.tools_result), change_map(src, self.tools_result, bg), Image.alpha_composite(self.base, self.tools_result))
        else:
            caps = ("BEFORE · on flat colour", "AFTER · on flat colour", "CHANGE MAP · red → empty, cyan → solid, yellow recoloured", "AFTER · over checker")
            imgs = (on_bg(src, bg), on_bg(self.tools_result, bg), change_map(src, self.tools_result, bg), self.tools_result)
        for lab, cap in zip(self.tcaps, caps): lab.setText(cap)
        for pv, im in zip(self.tviews, imgs): pv.set_image(im)
        hb, ha = alpha_hist(src), alpha_hist(self.tools_result)
        B = np.asarray(src)[..., 3]; A = np.asarray(self.tools_result)[..., 3]
        self.thist.setText(f"changes: {int(((B > 0) & (A == 0)).sum()):,} px become empty · {int(((B < 255) & (A == 255)).sum()):,} px become solid   |   alpha px  "
                           + "  ".join(f"{k}: {hb[k]:,}→{ha[k]:,}" for k in hb))

    def tools_store(self, k):
        if self.tools_result is None: return
        _, _, b2 = self.tslots[k]; self.tslots[k] = (self.tools_result, dict(self.tools_settings(), **self.tools_colour()), b2); b2.setEnabled(True); self.say(f"slot {k} stored")

    def tools_recall(self, k):
        img, st, _ = self.tslots[k]
        if st is None: return
        for w, key in ((self.t_lo, "lo"), (self.t_hi, "hi"), (self.t_trim, "trim_px"), (self.t_grow, "grow_px"), (self.t_fringe, "defringe_px"), (self.t_min, "min_px")): w.setValue(st[key])
        for w, key, f in ((self.t_hue, "hue", 1), (self.t_sat, "sat", 100), (self.t_light, "light", 1)):
            if key in st: w.setValue(int(round(st[key] * f)))
        self.tools_refresh(); self.say(f"slot {k} recalled")

    def tools_apply(self):
        if self.tools_result is None: return
        src, kind, it = self.tools_input()
        if kind == "layer":
            if not it: return
            f = self.layer_files()[it]; bak = os.path.join(self.side_dir(), f"{it}.layer.bak.png")
            if not os.path.exists(bak): self.tcache[1].save(bak)            # first fix keeps the original once — before any erasing
            fixed = self.tools_result; fixed.save(f)
            pv = os.path.join(self.args.out, "preview"); os.makedirs(pv, exist_ok=True)
            if self.base is not None: Image.fromarray(preview_sheet(self.base, fixed, self.item_slot.get(it))).save(os.path.join(pv, f"{it}.png"))
            icon = make_icon(fixed)
            if icon is not None: icon.save(os.path.join(self.args.out, f"icon-{it}.webp"), quality=90)
            self._gb = None; self.refresh_context(); self.say(f"{it}: layer-{it}.png rewritten (original kept at wardrobe/{it}.layer.bak.png), preview + icon regenerated")
            self.tcache = (None, None, None); self.tools_refresh(); return          # the fixed file is the new pristine input; erasing is baked in
        self.set_work(self.tools_result, (self.work_name or "source") + " (fixed)"); self.tools_refresh(); self.say("source = fixed image — Keep as base on the Base tab to save it")

    def tools_save(self):
        if self.tools_result is None: return
        p, _ = QFileDialog.getSaveFileName(self, "Save fixed image", os.path.join(self.args.out, "fixed.png"), "PNG (*.png)")
        if not p: return
        if not p.lower().endswith(".png"): p += ".png"
        self.tools_result.save(p); self.say(f"saved → {p}")

    def slider(self, lay, label, lo, hi, val, tt):
        s = tip(QSlider(Qt.Horizontal), tt); s.setRange(lo, hi); s.setValue(val); num = QLabel(str(val)); num.setFixedWidth(44)
        s.valueChanged.connect(lambda v: (num.setText(str(v)), self.schedule_cut()))
        lay.addWidget(row(tip(QLabel(label), tt), s, num)); return s

    def load_story_items(self, path):
        if not path: return {}
        try:
            s = json.load(open(path, encoding="utf-8"))
            return {k: v.get("wears") for k, v in (s.get("start_items") or {}).items() if isinstance(v, dict) and v.get("wears")}
        except Exception as e:
            print("story:", e); return {}

    def side_dir(self):
        d = os.path.join(self.args.out, "wardrobe"); os.makedirs(d, exist_ok=True); return d

    def layer_files(self):
        return {os.path.basename(f)[6:-4]: f for f in sorted(glob.glob(os.path.join(self.args.out, "layer-*.png"))) if not f.endswith("-back.png")}

    def slot_rank(self, it):
        sl = self.item_slot.get(it); return LAYER_ORDER.index(sl) if sl in LAYER_ORDER else 99

    def recipe(self, it):
        try: return json.load(open(os.path.join(self.side_dir(), f"{it}.json")))
        except Exception: return {}

    def refresh_context(self):
        old = {k: b.isChecked() for k, b in self.wear_boxes.items()}; self.wear_boxes = {}
        while self.ctx_lay.count():
            w = self.ctx_lay.takeAt(0).widget()
            if w: w.deleteLater()
        files = self.layer_files()
        for it in files:
            if it not in self.item_slot and self.recipe(it).get("slot"): self.item_slot[it] = self.recipe(it)["slot"]
        if not files: self.ctx_lay.addWidget(hint("(no layers saved yet)"), 0, 0)
        for i, it in enumerate(sorted(files, key=self.slot_rank)):
            b = QCheckBox(it); b.setChecked(old.get(it, False)); b.toggled.connect(lambda *_: self.context_changed())
            self.wear_boxes[it] = b; self.ctx_lay.addWidget(b, i // 2, i % 2)
        known = [NONE] + sorted(set(self.item_slot) | set(files)); cur = self.item.currentText()
        self.item.blockSignals(True); self.item.clear(); self.item.addItems(known); self.item.setCurrentText(cur); self.item.blockSignals(False)

    def context_changed(self):
        """The boxes decide what the NEXT Go sees (left pane). Results keep the context they were made with."""
        self.show_base()

    def worn(self):
        """The checked 'wear while generating' items — what the NEXT Go shows the model."""
        return tuple(i for i, b in self.wear_boxes.items() if b.isChecked())

    def ctx_image(self, on):
        """The base wearing the items `on`, as the card draws them (hair_back UNDER her). Every result is cut against
        the context it was generated with — never the live boxes, or wearing a shirt afterwards turns its whole area
        into 'change' and the hat loses to it (2026-10-02)."""
        if not self.base: return None
        on = tuple(i for i in on if i in self.layer_files()); files = self.layer_files()
        if self._gb is None: self._gb = {}
        if on not in self._gb: self._gb[on] = stack(self.base, [(it, pil_open(files[it])) for it in on], self.item_slot)
        return self._gb[on]

    def gen_base(self):
        """What the model sees and the next generation is cut against: the base with the checked layers worn."""
        return self.ctx_image(self.worn())

    def show_base(self):
        if self.base: self.lpreview.set_image(self.gen_base())

    def cur_item(self):
        """The typed item name; '' for the (none) entry."""
        it = self.item.currentText().strip(); return "" if it == NONE else it

    def item_changed(self, *_):
        it = self.cur_item()
        if it in self.item_slot and self.item_slot[it] in SLOTS: self.slot.setCurrentText(self.item_slot[it])
        elif slot_for(it): self.slot.setCurrentText(slot_for(it))   # black_leggings → pants, face_smile → face
        if it and self.prompt.toPlainText().strip() in (GARMENT_HINT, self._auto_prompt): self.auto_prompt(it)
        self.del_btn.setEnabled(bool(it and self.item_files(it)))
        if it and not self.recipe(it):                            # a NEW item starts from the defaults — settings never leak from the last item
            self.thresh.setValue(24); self.blob.setValue(400); self.skin.setChecked(True); self.trim.setValue(0); self.reach.setValue(2); self.keyon.setChecked(False)
        self.build_strip()

    def auto_prompt(self, it):
        slot = self.slot.currentText(); what = it.replace("_", " "); pref = slot.replace("_", " ") + " "
        if slot in SLOT_TPL and what.startswith(pref): what = what[len(pref):]      # face_smile → "smile", hair_back_long → "long"
        self._auto_prompt = SLOT_TPL.get(slot, GARMENT_TPL).format(what=what); self.prompt.setPlainText(self._auto_prompt)

    def slot_changed(self, *_):
        """Slot sets the cut's shape: pairs for shoes/socks, stick-out for hair/hat/coats, a head patch for face."""
        sl = self.slot.currentText()
        self.pieces.setValue(2 if sl in PAIRED else 1); self.outside.setChecked(sl in STICKS_OUT)
        it = self.cur_item()
        if it and self.prompt.toPlainText().strip() in (GARMENT_HINT, self._auto_prompt): self.auto_prompt(it)
        self.schedule_cut()

    def build_strip(self, pick=0):
        """The strip belongs to the picked item: first what it IS now — its saved layer worn ('saved'), or 'blank'
        when nothing is saved — then whatever was generated for it this session. Picking an item also brings its
        recipe back (slot, sliders, worn layers). Results never leak between items (2026-10-02)."""
        it = self.cur_item(); self._gb = None; self.show_base(); self.cut_cache = None
        if not self.base: self.results = []; self.sel = None; self.wstrip.set([], None); return
        files = self.layer_files(); rec = self.recipe(it) if it else {}
        if rec:
            if rec.get("slot") in SLOTS: self.slot.setCurrentText(rec["slot"])
            for w, key in ((self.thresh, "threshold"), (self.blob, "min_blob"), (self.pieces, "pieces")):
                if key in rec: w.setValue(rec[key])
            if "may_stick_out" in rec: self.outside.setChecked(rec["may_stick_out"])
            self.skin.setChecked(rec.get("skin_guard", True)); self.trim.setValue(float(rec.get("trim", 0))); self.reach.setValue(int(rec.get("reach", 2)))
            self.keyon.setChecked(bool(rec.get("key"))); self.keytol.setValue(int(rec.get("key_tol", 35)))
            if rec.get("key"): self.key = tuple(rec["key"]); self.show_key()
            for k, b in self.wear_boxes.items(): b.setChecked(k in (rec.get("worn_context") or []))
            self._gb = None; self.show_base()
        ctx = tuple(rec.get("worn_context") or [])
        if it in files:
            layer = pil_open(files[it]); self.results = [("saved", stack(self.ctx_image(ctx), [(it, layer)], {it: self.slot.currentText()}), dict(kind="saved", layer=layer, ctx=ctx))]
        else:
            self.results = [("blank", self.gen_base(), dict(kind="blank", ctx=()))]
        self.results += self.gens.get(it, [])
        if self._fresh and self.gens.get(it): self._fresh = False; pick = len(self.results) - 1      # a batch that landed while you were away shows itself once
        self.select(min(pick, len(self.results) - 1))

    def item_files(self, it):
        o = self.args.out
        return [f for f in (os.path.join(o, f"layer-{it}.png"), os.path.join(o, f"layer-{it}-back.png"), os.path.join(o, f"icon-{it}.webp"),
                            os.path.join(o, "preview", f"{it}.png"), os.path.join(self.side_dir(), f"{it}.gen.png"), os.path.join(self.side_dir(), f"{it}.json"))
                if os.path.exists(f)]

    def delete_item(self):
        it = self.item.currentText().strip(); files = self.item_files(it) if it else []
        if not files: return
        if QMessageBox.question(self, "delete", f"Delete {it}?\n\n" + "\n".join(os.path.relpath(f, self.args.out) for f in files)) != QMessageBox.Yes: return
        for f in files: os.remove(f)
        self.item_slot.pop(it, None); self.gens.pop(it, None); self.cut_cache = None; self.save_btn.setEnabled(False)
        self._gb = None; self.refresh_context(); self.item.setCurrentText(NONE)
        self.say(f"deleted {it} ({len(files)} files)")

    def go(self, refine=False):
        """Go: the model reads the base (+ worn layers). Refine: it reads the PICKED result instead — the prompt is
        the fix — and the new candidates join the strip. Either way the cut diffs against the base."""
        if not self.base: return QMessageBox.information(self, "go", "load a base first (Base tab → Keep, or Load base…)")
        prompt = self.prompt.toPlainText().strip()
        if not prompt: return QMessageBox.information(self, "go", "describe the garment")
        it = self.cur_item()
        if not it: return QMessageBox.information(self, "go", "name the item first — (none) is just for looking")
        if refine and (self.sel is None or self.results[self.sel][2]["kind"] == "blank"): return QMessageBox.information(self, "refine", "pick a result to refine first")
        ctx = self.results[self.sel][2]["ctx"] if refine else self.worn()           # a refined result keeps the context of the one it came from
        src = self.results[self.sel][1] if refine else self.gen_base(); ref, post = src, None; scope = self.wscope.checkedId()
        if scope:
            box = self.lpreview.visible_box()
            if not box: return QMessageBox.information(self, "go", "zoom the LEFT pane onto the region first (wheel), then Go — or pick 'whole image'")
            if scope == 1:
                ref, pad = crop_up(src, box)
                post = lambda img, src=src, box=box, pad=pad: paste_down(src, img, box, pad, model_alpha=True)
                self.say(f"zoomed region {box[2] - box[0]}×{box[3] - box[1]} → {ref.size[0]}×{ref.size[1]} for the model")
            else:
                ref, pad, big, wiped = blank_hole(src, box)
                post = lambda img, src=src, wiped=wiped, hole=box, big=big, pad=pad: paste_hole(src, wiped, img, hole, big, pad)
                self.say(f"replacing {box[2] - box[0]}×{box[3] - box[1]}; the model sees {big[2] - big[0]}×{big[3] - big[1]} → {ref.size[0]}×{ref.size[1]}")
        if not refine: self.gens[it] = []; self.build_strip(self.sel or 0)        # a new Go replaces this item's generations; Refine adds to them
        self._fresh = True
        self.generate(self.count.value(), prompt, ref, None, lambda seed, img, it=it, ctx=ctx: self.gen_done(it, ctx, seed, img), 0, self.neg.text().strip(), self.steps.value(), self.cfg.value(), post)

    def gen_done(self, it, ctx, seed, img):
        """A result for item `it`, generated over the context `ctx` — kept under that item even if another is picked
        meanwhile; the first of a batch is shown as it lands."""
        self.gens.setdefault(it, []).append((f"seed {seed}", img, dict(kind="new", seed=seed, ctx=ctx)))
        if it != self.cur_item(): return
        self.results.append(self.gens[it][-1]); self.wstrip.set(self.results, self.sel)
        if self._fresh: self._fresh = False; self.select(len(self.results) - 1)

    def select(self, i):
        """saved → the layer on disk as it is; blank → her alone; a result → cut with the sliders."""
        self.sel = i; self.wstrip.set(self.results, i); self.cut_timer.stop()
        label, img, e = self.results[i]; kind = e["kind"]
        self.refine_btn.setEnabled(kind != "blank"); self.save_btn.setEnabled(False)
        if kind == "saved": self.cut_cache = (label, None, e["layer"], np.asarray(e["layer"])[..., 3]); self.show_cut()
        elif kind == "blank": self.cut_cache = None; self.rpreview.set_image(img); self.say("nothing saved for this item yet — Go makes candidates")
        else: self.schedule_cut()

    def schedule_cut(self):
        if self.sel is not None: self.cut_timer.start()

    def do_cut(self):
        if self.sel is None or self.results[self.sel][2]["kind"] != "new": return
        _, gen, e = self.results[self.sel]; seed = e["seed"]
        layer, alpha = cut(self.ctx_image(e["ctx"]), gen, self.thresh.value(), self.blob.value(), not self.outside.isChecked(),
                           pieces=self.pieces.value(), head=(self.slot.currentText() == "face"), skin_guard=self.skin.isChecked(), trim=self.trim.value(),
                           reach=self.reach.value(), hair=(self.slot.currentText() in HAIR),
                           key=(self.key if self.keyon.isChecked() else None), key_tol=self.keytol.value())
        self.cut_cache = (seed, gen, layer, alpha); self.save_btn.setEnabled(True); self.show_cut()

    def show_cut(self):
        if not self.cut_cache: return
        seed, gen, layer, alpha = self.cut_cache; v = self.view.checkedId()
        if v == 0: img = gen if gen is not None else self.results[self.sel][1]      # 'saved' has no raw result: show it worn
        elif v == 1: img = layer
        elif v == 2: img = on_bg(layer, FLAT_BGS["dark"])
        elif v == 3: img = stack(self.base, [("it", layer)], {"it": self.slot.currentText()})             # the plain base + this item only
        else: img = stack(self.ctx_image(self.results[self.sel][2]["ctx"]), [("it", layer)], {"it": self.slot.currentText()})   # as the card will show it
        self.rpreview.set_image(img)
        px = int((alpha > 0).sum()); seed = seed if gen is not None else "saved layer"
        if gen is None: return self.say(f"{seed}: {px:,} px on disk — the sliders re-cut a result, not this; pick 'seed …' beside it to re-cut")
        if px < 200:
            self.say(f"seed {seed}: layer = {px} px — nothing found inside her outline. Switch view to 'result' to see what the model made; "
                     "if the change sticks out past her, tick 'may stick out'; if it's subtle, lower the threshold.")
        else:
            sl = self.slot.currentText(); tag = "  [cut as FACE: head patch]" if sl == "face" else ("  [HAIR_BACK: drawn under her]" if sl in UNDER else "")
            self.say(f"seed {seed}: layer = {px:,} px ({100 * px / alpha.size:.1f}% of frame){tag}")

    def save_layer(self):
        item = self.item.currentText().strip()
        if not item: return QMessageBox.information(self, "save", "name the item first")
        if not self.cut_cache: return QMessageBox.information(self, "save", "pick a result first")
        self.cut_timer.stop(); seed, gen, layer, alpha = self.cut_cache; slot = self.slot.currentText()
        if gen is None: return QMessageBox.information(self, "save", "that is the saved layer already")
        os.makedirs(self.args.out, exist_ok=True); pv = os.path.join(self.args.out, "preview"); os.makedirs(pv, exist_ok=True)
        out = os.path.join(self.args.out, f"layer-{item}.png")
        layer.save(out); gen.save(os.path.join(self.side_dir(), f"{item}.gen.png"))
        Image.fromarray(preview_sheet(self.base, layer, slot)).save(os.path.join(pv, f"{item}.png"))
        icon = make_icon(layer)
        if icon is not None: icon.save(os.path.join(self.args.out, f"icon-{item}.webp"), quality=90)
        self.item_slot[item] = slot
        json.dump({"item": item, "slot": slot, "seed": seed, "steps": self.steps.value(), "cfg": self.cfg.value(), "prompt": self.prompt.toPlainText().strip(),
                   "skin_guard": self.skin.isChecked(), "trim": self.trim.value(), "reach": self.reach.value(),
                   "key": (list(self.key) if self.keyon.isChecked() else None), "key_tol": self.keytol.value(),
                   "negative": self.neg.text().strip(), "reference": self.ref_name, "scope": ("whole", "region", "replace")[self.wscope.checkedId()],
                   "threshold": self.thresh.value(), "min_blob": self.blob.value(), "pieces": self.pieces.value(), "may_stick_out": self.outside.isChecked(),
                   "worn_context": list(self.results[self.sel][2]["ctx"]), "base": os.path.basename(self.base_path or "")},
                  open(os.path.join(self.side_dir(), f"{item}.json"), "w"), indent=1)
        self.gens[item] = [e for e in self.gens.get(item, []) if e[2].get("seed") != seed]      # it is the 'saved' entry now
        self.say(f"saved → {out}"); self._gb = None; self.refresh_context(); self.del_btn.setEnabled(True); self.build_strip(0)
        self.save_btn.setText(f"Saved ✓  layer-{item}.png"); QTimer.singleShot(4000, lambda: self.save_btn.setText("Save layer"))


def dark(app):
    app.setStyle("Fusion"); p = QPalette()
    for role, c in ((QPalette.Window, "#202124"), (QPalette.WindowText, "#e8e8e8"), (QPalette.Base, "#2a2b2e"), (QPalette.AlternateBase, "#323337"),
                    (QPalette.Text, "#e8e8e8"), (QPalette.Button, "#2f3034"), (QPalette.ButtonText, "#e8e8e8"), (QPalette.Highlight, "#3b6fd6"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#2a2b2e"), (QPalette.ToolTipText, "#e8e8e8")):
        p.setColor(role, QColor(c))
    app.setPalette(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--story", help="story.json — seeds the item dropdown from its start_items; --out defaults to its backdrops/")
    ap.add_argument("--base", help="base PNG (RGBA) to open straight on the Wardrobe tab")
    ap.add_argument("--out", default="layers", help="where <name>-base.png, layer-<item>.png, preview/ and wardrobe/ land")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--workflow", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "qwen21-edit.api.json"))
    a = ap.parse_args()
    if a.story and a.out == "layers": a.out = os.path.join(os.path.dirname(os.path.abspath(a.story)), "backdrops")
    app = QApplication(sys.argv); dark(app); w = App(a); w.show(); sys.exit(app.exec())
