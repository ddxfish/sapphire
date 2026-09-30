#!/usr/bin/env python
"""wardrobe — base + garment LAYER maker for Game Room story packs.
Qwen-Image-2.1 through a RUNNING ComfyUI (default http://127.0.0.1:8188); no models on this side.

    conda activate wardrobe
    python tools/wardrobe/wardrobe.py --story path/to/story.json      # layers land in the pack's backdrops/
    python tools/wardrobe/wardrobe.py --base backdrops/sapphire-base.png --out backdrops/

Base tab:     Go ×N from text → click the best → "refine selected" ("put her hair behind her back") → Go ×N
              → click the best → Keep as base. Bases are RGBA on a transparent background.
Wardrobe tab: pick the item, "Add black leggings", Go ×N, click one, Save layer → layer-<item>.png.

The cut: a layer holds only pixels that CHANGED and sit inside the base's own alpha, so the model's
re-rendered edge halo can never reach a layer. "May stick out" (coats, hats, bags) lets the layer grow past
her silhouette and erodes the new edge instead.
"""
import argparse, copy, hashlib, io, json, os, sys, threading, time
import numpy as np, cv2, requests
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME = (736, 1216)     # the portrait frame: Qwen-Image-2.1 works in multiples of 32; the card is percent-based
SCALE = 2.0
THUMB = 150
CANVAS_H = 960
LAYER_ORDER = ["underwear", "bra", "socks", "pants", "shirt", "shoes", "body", "jacket", "outer", "hat", "in_hand"]
RGBA = "This is an RGBA image with transparency. {body} The image has alpha channel and the background is transparent."
BASE_PROMPT = RGBA.format(body="A full-body photo of a woman standing straight, facing the camera, arms relaxed at her sides, "
                               "feet slightly apart, wearing nothing, whole body in frame with space above the head and "
                               "below the feet, even soft studio lighting.")
REF_PROMPT = RGBA.format(body="The same person as in the reference image, same face and hair. Zoom out to a full-body photo: "
                              "standing straight, facing the camera, arms relaxed at her sides, feet slightly apart, wearing nothing, "
                              "whole body in frame with space above the head and below the feet, even soft studio lighting.")
EDIT_HINT = "Make her expression neutral and put her hair behind her head. Keep everything else exactly the same."
GARMENT_HINT = "Add black leggings. Keep everything else exactly the same, keep the transparent background."


# ---------------------------------------------------------------- Comfy
class Comfy:
    """The three calls we need: upload a reference, queue a graph, fetch the PNG."""

    def __init__(self, url):
        self.url = url.rstrip("/"); self.cid = hashlib.md5(os.urandom(8)).hexdigest()

    def upload(self, img):
        buf = io.BytesIO(); img.save(buf, format="PNG"); data = buf.getvalue()
        name = f"wardrobe-{hashlib.sha1(data).hexdigest()[:10]}.png"      # same image → same file, no re-upload churn
        r = requests.post(self.url + "/upload/image", files={"image": (name, data, "image/png")},
                          data={"overwrite": "true"}, timeout=120); r.raise_for_status()
        j = r.json(); return (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]

    def run(self, graph, stop=None):
        r = requests.post(self.url + "/prompt", json={"prompt": graph, "client_id": self.cid}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"comfy refused the graph: {r.text[:800]}")
        pid = r.json()["prompt_id"]
        while True:
            if stop is not None and stop.is_set():
                requests.post(self.url + "/interrupt", timeout=10); raise RuntimeError("stopped")
            h = requests.get(f"{self.url}/history/{pid}", timeout=30).json().get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msg = [m[1].get("exception_message") for m in st.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError("comfy error: " + (msg[0] if msg else "see the comfy console"))
                imgs = [im for o in h.get("outputs", {}).values() for im in o.get("images", [])]
                if imgs:
                    im = imgs[0]
                    r = requests.get(self.url + "/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                                 "type": im.get("type", "output")}, timeout=120)
                    r.raise_for_status(); return Image.open(io.BytesIO(r.content)).convert("RGBA")
            time.sleep(0.4)


def _by_class(g, cls):
    return [k for k, v in g.items() if v.get("class_type", "").startswith(cls)]


def build_graph(tpl, prompt, seed, steps, ref_name=None, size=None, res=0):
    """Patch the exported edit workflow: literal prompt, seed, and one of three shapes —
    edit (ref, no size): the sampler starts from the encoder's latent, output = the reference's size;
    reference (ref + size): empty latent at `size`, the reference only conditions (a face → a full body);
    text (no ref): empty latent at `size`.
    Both switch nodes are bypassed by wiring around them; everything not feeding the save node is pruned."""
    g = copy.deepcopy(tpl)
    save = _by_class(g, "SaveImage")[0]; samp = _by_class(g, "KSampler")[0]
    enc = g[samp]["inputs"]["positive"][0]; e = g[enc]["inputs"]
    e["prompt"] = prompt; e["negative_prompt"] = ""; e["resolution"] = int(res)   # 0 = keep the reference at its own size
    s = g[samp]["inputs"]; s["seed"] = int(seed); s["steps"] = int(steps)
    if ref_name:
        load = _by_class(g, "LoadImage")[0]
        g[load]["inputs"]["image"] = ref_name; e["images.image_1"] = [load, 0]
    else:
        e.pop("images.image_1", None)
    if ref_name and not size:
        s["latent_image"] = [enc, 2]
    else:
        empty = _by_class(g, "EmptyLatentImage")[0]
        g[empty]["inputs"].update(width=size[0], height=size[1], batch_size=1)
        s["latent_image"] = [empty, 0]
    g[save]["inputs"]["filename_prefix"] = "wardrobe/w"
    keep, todo = set(), [save]                                   # prune: closure of the save node
    while todo:
        n = todo.pop()
        if n in keep or n not in g: continue
        keep.add(n)
        for v in g[n]["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str): todo.append(v[0])
    return {k: g[k] for k in keep}


# ---------------------------------------------------------------- cut
def cut(base, gen, thresh=24, min_blob=400, inside=True, feather=2):
    """base, gen: RGBA PIL, same size. Returns (layer RGBA PIL, alpha ndarray).
    inside=True: only pixels inside the base's own alpha may change — the halo the model paints around her
    edge is outside it, so it never gets in. inside=False: new opaque pixels outside her count too (coats,
    hats), and that outer edge is eroded a few px to shave the halo."""
    A = np.asarray(base, dtype=np.int16); B = np.asarray(gen, dtype=np.int16)
    aA, aB = A[..., 3], B[..., 3]
    sil = (aA > 8).astype(np.uint8)
    rgb = np.abs(A[..., :3] - B[..., :3]).max(axis=2)
    m = (rgb > thresh) & (sil > 0)
    if not inside:
        m |= (aB > 8) & (sil == 0)
    m = m.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))   # edge ribbons die here
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))                              # pinholes
    if min_blob > 0:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        ok = np.zeros(n, bool); ok[1:] = st[1:, cv2.CC_STAT_AREA] >= min_blob
        m = ok[lab].astype(np.uint8)
    if not inside:                                                # shave the model's halo off the new outer edge
        er = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        m = np.where(sil > 0, m, er).astype(np.uint8)
    a = m * 255
    if feather > 0:
        k = feather * 2 + 1; a = cv2.GaussianBlur(a, (k, k), 0)
    under = np.where(sil > 0, aA, 0 if inside else aB)             # never more opaque than the pixel under it
    a = np.minimum(a, under).astype(np.uint8)
    out = np.dstack([np.clip(B[..., :3], 0, 255).astype(np.uint8), a])
    out[a == 0, :3] = 0                                              # transparent = black: small file, leaks nothing
    return Image.fromarray(out), a


def checker(size, cell=16):
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    v = (((x // cell) + (y // cell)) % 2 * 40 + 170).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v, v, np.full_like(v, 255)]))


class Strip(tk.Frame):
    """Full-width thumbnail strip: a canvas holding a frame, horizontal scrollbar when it overflows,
    wheel (or shift+wheel) scrolls sideways while the pointer is over it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, height=THUMB + 24, highlightthickness=0)
        self.sb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.sb.set)
        self.canvas.pack(fill="x"); self.sb.pack(fill="x")
        self.inner = tk.Frame(self.canvas)
        self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        for ev, d in (("<Button-4>", -1), ("<Button-5>", 1), ("<Shift-Button-4>", -1), ("<Shift-Button-5>", 1)):
            self.canvas.bind_all(ev, lambda e, d=d: self._wheel(e, d), add="+")
        self.canvas.bind_all("<MouseWheel>", lambda e: self._wheel(e, -1 if e.delta > 0 else 1), add="+")

    def _wheel(self, e, d):
        w = e.widget
        while w is not None and w is not self:
            w = getattr(w, "master", None)
        if w is self: self.canvas.xview_scroll(d * 3, "units")


def filepick(save=False, title="", initial=""):
    """The system's file dialog (zenity = GTK on Ubuntu); tk's own as the fallback."""
    import shutil, subprocess
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--title", title, "--file-filter=images | *.png *.webp *.jpg *.jpeg", "--file-filter=all | *"]
        if save: cmd += ["--save", "--confirm-overwrite"]
        if initial: cmd += ["--filename", initial]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=900).stdout.strip() or None
        except Exception as e:
            print("zenity:", e)
    fn = filedialog.asksaveasfilename if save else filedialog.askopenfilename
    return fn(title=title, defaultextension=".png", filetypes=[("images", "*.png *.webp *.jpg *.jpeg")]) or None


def hint(parent, text):
    tk.Label(parent, text=text, fg="#8a8a8a", wraplength=int(500 * SCALE), justify="left", anchor="w",
             font=("", max(7, int(7 * SCALE)))).pack(fill="x", padx=8, pady=(0, 4))


def big(parent, text, color, cmd):
    return tk.Button(parent, text=text, bg=color, fg="white", font=("", int(12 * SCALE), "bold"), command=cmd)


# ---------------------------------------------------------------- app
class App:
    def __init__(self, root, args):
        global SCALE, THUMB, CANVAS_H
        self.root, self.args = root, args
        SCALE = args.scale; THUMB = int(150 * SCALE); CANVAS_H = int(root.winfo_screenheight() * 0.84)
        for n in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont"):
            f = tkfont.nametofont(n); f.configure(size=int(abs(f.cget("size")) * SCALE))
        st = ttk.Style(); st.configure("TCombobox", arrowsize=int(14 * SCALE))
        st.configure("TNotebook.Tab", font=("", int(11 * SCALE), "bold"), padding=(int(14 * SCALE), int(6 * SCALE)))
        root.option_add("*TCombobox*Listbox.font", tkfont.nametofont("TkDefaultFont"))
        root.title("wardrobe — Qwen-Image-2.1 via ComfyUI")
        self.tpl = json.load(open(args.workflow))
        self.comfy = Comfy(args.comfy); self.stop = threading.Event(); self.busy = False
        self.base = None; self.base_path = None
        self.bresults = []; self.bsel = None                          # base tab candidates / selection
        self.results = []; self.sel = None; self.cut_cache = None; self._gb = None
        self.item_slot = self.load_story_items(args.story)
        self.build()
        if args.base: self.load_base(args.base)

    # ---------- shared ----------
    def say(self, t): self.status.config(text=t)

    def build(self):
        r = self.root
        top = tk.Frame(r); top.pack(fill="x", padx=6, pady=4)
        tk.Label(top, text="comfy").pack(side="left")
        self.url = tk.StringVar(value=self.args.comfy); tk.Entry(top, textvariable=self.url, width=26).pack(side="left", padx=(4, 12))
        tk.Label(top, text="steps").pack(side="left"); self.steps = tk.IntVar(value=25); tk.Entry(top, textvariable=self.steps, width=3).pack(side="left", padx=(2, 12))
        self.stop_btn = tk.Button(top, text="Stop", command=self.stop.set, state="disabled"); self.stop_btn.pack(side="left")
        self.status = tk.Label(top, text="", anchor="w", fg="#59f"); self.status.pack(side="left", fill="x", expand=True, padx=12)
        self.nb = ttk.Notebook(r); self.nb.pack(fill="both", expand=True)
        self.build_base_tab(); self.build_wardrobe_tab()

    def generate(self, n, prompt, ref, size, done, res=0):
        """Run n seeds on a worker thread; done(seed, img) on the UI thread per result.
        size=None: an edit, results match the reference's size. size given: results come at that size."""
        if self.busy: return self.say("busy — Stop first")
        self.busy = True; self.stop.clear(); self.stop_btn.config(state="normal")
        self.comfy = Comfy(self.url.get())

        def work():
            base_seed = int(time.time()) % 1_000_000
            try:
                ref_name = self.comfy.upload(ref) if ref is not None else None
                for i in range(n):
                    seed = base_seed + i * 7919; t0 = time.time()
                    self.root.after(0, self.say, f"{i + 1}/{n} seed {seed} …")
                    g = build_graph(self.tpl, prompt, seed, self.steps.get(), ref_name, size, res)
                    img = self.comfy.run(g, self.stop)
                    want = size or ref.size
                    if img.size != want: img = img.resize(want, Image.LANCZOS)
                    self.root.after(0, done, seed, img)
                    self.root.after(0, self.say, f"{i + 1}/{n} in {time.time() - t0:.1f}s — click a thumbnail")
            except Exception as e:
                self.root.after(0, self.say, f"{e}"[:200])
            self.busy = False; self.root.after(0, self.stop_btn.config, {"state": "disabled"})
        threading.Thread(target=work, daemon=True).start()

    def strip(self, frame, results, sel, on_click):
        for w in frame.winfo_children(): w.destroy()
        frame._thumbs = []
        for i, (seed, img) in enumerate(results):
            t = img.copy(); t.thumbnail((THUMB, THUMB)); t = Image.alpha_composite(checker(t.size, 8), t)
            ph = ImageTk.PhotoImage(t); frame._thumbs.append(ph)
            b = tk.Button(frame, image=ph, relief="sunken" if i == sel else "raised", command=lambda i=i: on_click(i))
            b.pack(side="left", padx=2)
            tk.Label(frame, text=str(seed), font=("", int(7 * SCALE))).place(in_=b, relx=0, rely=1, anchor="sw")

    def fit(self, img, label):
        w, h = label.winfo_width() or 600, label.winfo_height() or 800
        img = Image.alpha_composite(checker(img.size), img).convert("RGB"); img.thumbnail((w, h), Image.LANCZOS)
        ph = ImageTk.PhotoImage(img); label._ph = ph; label.config(image=ph)

    # ---------- base tab ----------
    # One WORKING IMAGE. Load it, or click a candidate to promote it. Every Go reads the working image:
    #   edit this           → same size, the prompt is the change (neutral face, hair back, smile less)
    #   full body from this → 736×1216 canvas, the working image is the reference (a face → a body)
    #   from text           → no image at all
    def build_base_tab(self):
        tab = tk.Frame(self.nb); self.nb.add(tab, text="  Base  ")
        self.bstrip = Strip(tab); self.bstrip.pack(side="bottom", fill="x", padx=6, pady=4)
        left = tk.Frame(tab); left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(tab, width=int(520 * SCALE)); right.pack(side="right", fill="y"); right.pack_propagate(False)
        pane = tk.Frame(left); pane.pack(fill="both", expand=True)
        lcol = tk.Frame(pane); lcol.pack(side="left", fill="both", expand=True, padx=(6, 3))
        rcol = tk.Frame(pane); rcol.pack(side="left", fill="both", expand=True, padx=(3, 6))
        self.worklab = tk.Label(lcol, text="WORKING IMAGE — what Go reads: (none — Load image…, or Go from text)",
                                fg="#ddd", anchor="w", font=("", int(10 * SCALE), "bold"))
        self.worklab.pack(fill="x", pady=4)
        self.bpreview = tk.Label(lcol, bg="#111"); self.bpreview.pack(fill="both", expand=True, pady=(0, 6))
        self.candlab = tk.Label(rcol, text="SELECTED CANDIDATE — click a thumbnail", fg="#ddd", anchor="w", font=("", int(10 * SCALE), "bold"))
        self.candlab.pack(fill="x", pady=4)
        self.cpreview = tk.Label(rcol, bg="#111"); self.cpreview.pack(fill="both", expand=True, pady=(0, 6))
        self.work = None; self.work_name = ""

        f = tk.Frame(right); f.pack(fill="x", padx=6, pady=(8, 0))
        tk.Label(f, text="name", width=9, anchor="w").pack(side="left")
        self.bname = tk.StringVar(value="sapphire"); tk.Entry(f, textvariable=self.bname).pack(side="left", fill="x", expand=True)
        hint(right, "Character id. Keep as base saves <name>-base.png into the pack's backdrops/. Point the cast `image` at it.")
        f = tk.Frame(right); f.pack(fill="x", padx=6)
        tk.Button(f, text="Load image…", command=self.pick_work).pack(side="left")
        tk.Button(f, text="Save image…", command=self.save_work).pack(side="left", padx=6)
        hint(right, "Load = make any file the working image (her face). Save = write the working image anywhere as a PNG "
                    "(a checkpoint you can Load again later). Clicking a candidate below makes IT the working image.")
        self.bmode = tk.StringVar(value="edit")
        f = tk.Frame(right); f.pack(fill="x", padx=6)
        for t, v in (("edit this", "edit"), ("full body from this", "body"), ("from text", "text")):
            tk.Radiobutton(f, text=t, value=v, variable=self.bmode, command=self.mode_changed).pack(side="left", padx=(0, 8))
        hint(right, f"edit this: the working image at its own size, the prompt is the change. full body from this: a "
                    f"{FRAME[0]}×{FRAME[1]} canvas with the working image as the reference (a face → a body). "
                    "from text: nothing but the prompt.")
        tk.Label(right, text="prompt", anchor="w").pack(fill="x", padx=6)
        self.bprompt = tk.Text(right, height=8, wrap="word"); self.bprompt.pack(fill="x", padx=6); self.bprompt.insert("1.0", EDIT_HINT)
        hint(right, "Body/text prompts keep the RGBA sentences at both ends — that's what makes the background transparent. "
                    "The prefill only swaps with the mode while you haven't edited it.")
        f = tk.Frame(right); f.pack(fill="x", padx=6, pady=2)
        self.bcount = tk.IntVar(value=4)
        tk.Label(f, text="×seeds").pack(side="left"); tk.Entry(f, textvariable=self.bcount, width=3).pack(side="left", padx=(2, 10))
        self.bgo = big(right, "Go", "#2a6", self.base_go); self.bgo.pack(fill="x", padx=6, pady=6)
        hint(right, "Candidates land in the strip. Click one to see it beside the working image.")
        self.buse = big(right, "◀ Use selected as working image", "#a52", self.use_selected); self.buse.pack(fill="x", padx=6, pady=6); self.buse.config(state="disabled")
        hint(right, "Promotes the selected candidate: the next Go reads IT. Loop: Go → click → Use → change the prompt → Go …")
        self.bkeep = big(right, "Keep as base →", "#36c", self.base_keep); self.bkeep.pack(fill="x", padx=6, pady=6); self.bkeep.config(state="disabled")
        hint(right, "The WORKING image becomes <name>-base.png and the Wardrobe tab opens on it.")

    DEFAULTS = {"edit": EDIT_HINT, "body": REF_PROMPT, "text": BASE_PROMPT}

    def mode_changed(self):
        if self.bprompt.get("1.0", "end").strip() in self.DEFAULTS.values():
            self.bprompt.delete("1.0", "end"); self.bprompt.insert("1.0", self.DEFAULTS[self.bmode.get()])

    def set_work(self, img, name):
        self.work, self.work_name = img, name
        self.worklab.config(text=f"WORKING IMAGE — what Go reads: {name}  {img.size[0]}×{img.size[1]}")
        self.fit(img, self.bpreview); self.bkeep.config(state="normal")

    def use_selected(self):
        if self.bsel is None: return
        seed, img = self.bresults[self.bsel]; self.set_work(img, f"candidate seed {seed}")
        self.say(f"working image = candidate seed {seed} — Go now reads it")

    def pick_work(self):
        p = filepick(title="Load working image")
        if p: self.set_work(Image.open(p).convert("RGBA"), os.path.basename(p))

    def save_work(self):
        if self.work is None: return messagebox.showinfo("save", "nothing to save yet")
        p = filepick(save=True, title="Save working image", initial=os.path.join(self.side_dir(), f"{self.bname.get().strip() or 'work'}-work.png"))
        if not p: return
        if not p.lower().endswith(".png"): p += ".png"
        self.work.save(p); self.say(f"saved → {p}")

    def base_go(self):
        prompt = self.bprompt.get("1.0", "end").strip()
        if not prompt: return messagebox.showinfo("go", "write a prompt")
        mode = self.bmode.get(); ref, size, res = None, FRAME, 0
        if mode != "text" and self.work is None:
            return messagebox.showinfo("go", "no working image — Load image…, or click a candidate")
        if mode == "edit": ref, size = self.work, None
        elif mode == "body": ref, res = self.work, 1024          # the face is conditioning, not the canvas
        self.bresults = []; self.bsel = None; self.strip(self.bstrip.inner, [], None, self.bselect)
        self.buse.config(state="disabled"); self.cpreview.config(image=""); self.candlab.config(text="SELECTED CANDIDATE — click a thumbnail")
        self.generate(self.bcount.get(), prompt, ref, size, self.base_done, res)

    def base_done(self, seed, img):
        self.bresults.append((seed, img)); self.strip(self.bstrip.inner, self.bresults, self.bsel, self.bselect)

    def bselect(self, i):
        self.bsel = i; self.strip(self.bstrip.inner, self.bresults, i, self.bselect)
        seed, img = self.bresults[i]
        self.candlab.config(text=f"SELECTED CANDIDATE — seed {seed}  {img.size[0]}×{img.size[1]}")
        self.fit(img, self.cpreview); self.buse.config(state="normal")

    def base_keep(self):
        if self.work is None: return
        name = self.bname.get().strip() or "base"
        os.makedirs(self.args.out, exist_ok=True)
        out = os.path.join(self.args.out, f"{name}-base.png"); self.work.save(out)
        json.dump({"name": name, "from": self.work_name, "mode": self.bmode.get(), "steps": self.steps.get(),
                   "prompt": self.bprompt.get("1.0", "end").strip()}, open(os.path.join(self.side_dir(), f"{name}-base.json"), "w"), indent=1)
        self.load_base(out); self.say(f"base saved → {out}")

    def pick_base(self):
        p = filepick(title="Load base"); p and self.load_base(p)

    def load_base(self, path):
        self.base = Image.open(path).convert("RGBA"); self.base_path = path
        if os.path.basename(path).endswith("-base.png"): self.bname.set(os.path.basename(path)[:-9])
        self.set_work(self.base, os.path.basename(path))
        self.results = []; self.sel = None; self.cut_cache = None; self._gb = None
        self.strip(self.wstrip.inner, [], None, self.select); self.rpreview.config(image=""); self.refresh_context(); self.show_base()
        self.say(f"base {os.path.basename(path)} {self.base.size[0]}×{self.base.size[1]}"); self.nb.select(1)

    # ---------- wardrobe tab ----------
    def build_wardrobe_tab(self):
        tab = tk.Frame(self.nb); self.nb.add(tab, text="  Wardrobe  ")
        self.wstrip = Strip(tab); self.wstrip.pack(side="bottom", fill="x", padx=6, pady=4)
        left = tk.Frame(tab); left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(tab, width=int(520 * SCALE)); right.pack(side="right", fill="y"); right.pack_propagate(False)
        tk.Label(left, text="LEFT: the base she's generated on (with any worn layers). RIGHT: the picked result, cut view below.",
                 fg="#8a8a8a", anchor="w").pack(fill="x", padx=8, pady=4)
        pane = tk.Frame(left); pane.pack(fill="both", expand=True)
        self.lpreview = tk.Label(pane, bg="#111"); self.lpreview.pack(side="left", fill="both", expand=True, padx=(6, 3), pady=6)
        self.rpreview = tk.Label(pane, bg="#111"); self.rpreview.pack(side="left", fill="both", expand=True, padx=(3, 6), pady=6)

        f = tk.Frame(right); f.pack(fill="x", padx=6, pady=(8, 0))
        tk.Label(f, text="item", width=9, anchor="w").pack(side="left")
        self.item = tk.StringVar()
        ttk.Combobox(f, textvariable=self.item, values=sorted(self.item_slot)).pack(side="left", fill="x", expand=True)
        self.item.trace_add("write", lambda *a: self.maybe_reload_item())
        hint(right, "The story's item key (underscores, e.g. black_leggings — dropdown = this pack's wearables). "
                    "Saves as layer-<item>.png; the card paints it whenever the item is worn.")
        tk.Label(right, text="wear while generating", anchor="w").pack(fill="x", padx=6)
        self.ctx = tk.Frame(right); self.ctx.pack(fill="x", padx=6)
        hint(right, "Stack saved layers under THIS generation — wear the top while making the blazer. "
                    "The new layer still holds only the new garment.")
        tk.Label(right, text="prompt", anchor="w").pack(fill="x", padx=6)
        self.prompt = tk.Text(right, height=4, wrap="word"); self.prompt.pack(fill="x", padx=6); self.prompt.insert("1.0", GARMENT_HINT)
        hint(right, "The CHANGE only. Don't describe her — she is the reference.")
        f = tk.Frame(right); f.pack(fill="x", padx=6, pady=2)
        self.count = tk.IntVar(value=4)
        tk.Label(f, text="×seeds").pack(side="left"); tk.Entry(f, textvariable=self.count, width=3).pack(side="left", padx=(2, 10))
        self.go_btn = big(right, "Go", "#2a6", self.go); self.go_btn.pack(fill="x", padx=6, pady=4)

        c = tk.LabelFrame(right, text="Cut"); c.pack(fill="both", expand=True, padx=6, pady=4)
        self.thresh = tk.IntVar(value=24); self.blob = tk.IntVar(value=400)
        for lbl, var, lo, hi, h in (("threshold", self.thresh, 4, 80, "how different a pixel must be to count as garment"),
                                    ("min blob px", self.blob, 0, 5000, "islands smaller than this are dropped")):
            f = tk.Frame(c); f.pack(fill="x")
            tk.Label(f, text=lbl, width=11, anchor="w").pack(side="left")
            tk.Scale(f, from_=lo, to=hi, orient="horizontal", variable=var, showvalue=True, command=lambda *a: self.schedule_cut()).pack(side="left", fill="x", expand=True)
            tk.Label(f, text=h, fg="#8a8a8a", font=("", max(7, int(7 * SCALE)))).pack(side="left", padx=6)
        self.outside = tk.BooleanVar(value=False)
        tk.Checkbutton(c, text="may stick out past her (coats, hats, bags)", variable=self.outside, command=self.schedule_cut).pack(anchor="w")
        hint(c, "OFF: the layer can only exist inside the base's own alpha — the model's edge halo never gets in. "
                "ON: new pixels outside her count too, and that outer edge is shaved a few px.")
        self.view = tk.StringVar(value="over base")
        vf = tk.Frame(c); vf.pack(fill="x")
        for v in ("result", "layer", "over base"):
            tk.Radiobutton(vf, text=v, value=v, variable=self.view, command=self.show_cut).pack(side="left")
        hint(c, "result = what the model made · layer = the cut-out alone · over base = stacked, as the card shows it.")
        self.save_btn = big(c, "Save layer", "#36c", self.save_layer); self.save_btn.pack(fill="x", pady=4); self.save_btn.config(state="disabled")
        hint(c, "Writes backdrops/layer-<item>.png + the recipe and raw result in backdrops/wardrobe/.")

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
        import glob
        return {os.path.basename(f)[6:-4]: f for f in sorted(glob.glob(os.path.join(self.args.out, "layer-*.png")))}

    def slot_rank(self, it):
        sl = self.item_slot.get(it); return LAYER_ORDER.index(sl) if sl in LAYER_ORDER else 99

    def refresh_context(self):
        for w in self.ctx.winfo_children(): w.destroy()
        old = getattr(self, "wear_vars", {}); self.wear_vars = {}
        files = self.layer_files()
        if not files:
            tk.Label(self.ctx, text="(no layers saved yet)", fg="#8a8a8a").pack(side="left"); return
        for it in sorted(files, key=self.slot_rank):
            v = tk.BooleanVar(value=old[it].get() if it in old else False); self.wear_vars[it] = v
            tk.Checkbutton(self.ctx, text=it, variable=v, command=self.context_changed).pack(side="left", padx=(0, 6))

    def context_changed(self):
        self._gb = None; self.show_base()
        if self.cut_cache: self.schedule_cut()

    def gen_base(self):
        """The base with the checked layers stacked on it — what the model sees and what the cut diffs against."""
        if not self.base: return None
        on = tuple(i for i, v in getattr(self, "wear_vars", {}).items() if v.get() and i != self.item.get().strip())
        if self._gb and self._gb[0] == on: return self._gb[1]
        img = self.base.copy(); files = self.layer_files()
        for it in sorted(on, key=self.slot_rank):
            img = Image.alpha_composite(img, Image.open(files[it]).convert("RGBA").resize(img.size))
        self._gb = (on, img); return img

    def show_base(self):
        if self.base: self.fit(self.gen_base(), self.lpreview)

    def maybe_reload_item(self):
        """Picking an item with a saved raw result reloads it — re-cut without regenerating."""
        it = self.item.get().strip(); g = os.path.join(self.side_dir(), f"{it}.gen.png") if it else None
        self._gb = None; self.show_base()
        if g and os.path.exists(g) and not self.results and self.base:
            try:
                rec = json.load(open(os.path.join(self.side_dir(), f"{it}.json")))
                self.results = [(rec.get("seed", 0), Image.open(g).convert("RGBA"))]
                self.strip(self.wstrip.inner, self.results, None, self.select); self.select(0)
                self.say(f"loaded last result for {it} — re-cut or Go for new ones")
            except Exception as e: print("reload:", e)

    def go(self):
        if not self.base: return messagebox.showinfo("go", "load a base first (Base tab → Keep, or Load base…)")
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt: return messagebox.showinfo("go", "describe the garment")
        self.results = []; self.sel = None; self.cut_cache = None; self.save_btn.config(state="disabled")
        self.strip(self.wstrip.inner, [], None, self.select)
        self.generate(self.count.get(), prompt, self.gen_base(), None, self.gen_done)

    def gen_done(self, seed, img):
        self.results.append((seed, img)); self.strip(self.wstrip.inner, self.results, self.sel, self.select)
        if self.sel is None: self.select(0)

    def select(self, i):
        self.sel = i; self.strip(self.wstrip.inner, self.results, i, self.select); self.schedule_cut()

    def schedule_cut(self):
        if self.sel is None: return
        if getattr(self, "_cut_job", None): self.root.after_cancel(self._cut_job)
        self._cut_job = self.root.after(120, self.do_cut)

    def do_cut(self):
        self._cut_job = None
        if self.sel is None: return
        seed, gen = self.results[self.sel]
        layer, alpha = cut(self.gen_base(), gen, self.thresh.get(), self.blob.get(), not self.outside.get())
        self.cut_cache = (seed, gen, layer, alpha); self.save_btn.config(state="normal"); self.show_cut()

    def show_cut(self):
        if not self.cut_cache: return
        seed, gen, layer, alpha = self.cut_cache; v = self.view.get()
        img = gen if v == "result" else layer if v == "layer" else Image.alpha_composite(self.gen_base(), layer)
        self.fit(img, self.rpreview)
        px = int((alpha > 0).sum())
        self.say(f"seed {seed}: layer = {px:,} px ({100 * px / alpha.size:.1f}% of frame)")

    def save_layer(self):
        item = self.item.get().strip()
        if not item: return messagebox.showinfo("save", "name the item (matches the story's start_items key)")
        seed, gen, layer, alpha = self.cut_cache
        os.makedirs(self.args.out, exist_ok=True)
        out = os.path.join(self.args.out, f"layer-{item}.png"); layer.save(out)
        gen.save(os.path.join(self.side_dir(), f"{item}.gen.png"))
        json.dump({"item": item, "seed": seed, "steps": self.steps.get(), "prompt": self.prompt.get("1.0", "end").strip(),
                   "threshold": self.thresh.get(), "min_blob": self.blob.get(), "may_stick_out": self.outside.get(),
                   "worn_context": [i for i, v in self.wear_vars.items() if v.get()], "base": os.path.basename(self.base_path or "")},
                  open(os.path.join(self.side_dir(), f"{item}.json"), "w"), indent=1)
        self.say(f"saved → {out}"); self._gb = None; self.refresh_context()
        self.save_btn.config(text=f"Saved ✓  layer-{item}.png", bg="#2a6")
        self.root.after(4000, lambda: self.save_btn.config(text="Save layer", bg="#36c"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--story", help="story.json — item names come from its start_items with a 'wears' slot; --out defaults to its backdrops/")
    ap.add_argument("--base", help="base PNG (RGBA) to open straight on the Wardrobe tab")
    ap.add_argument("--out", default="layers", help="where <name>-base.png and layer-<item>.png land (sidecars in wardrobe/)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--workflow", default=os.path.join(HERE, "qwen21-edit.api.json"), help="ComfyUI API-format export of the 2.1 edit workflow")
    ap.add_argument("--scale", type=float, default=2.0, help="UI scale: 2 for 4K (default), 1 for 1080p")
    a = ap.parse_args()
    if a.story and a.out == "layers":
        a.out = os.path.join(os.path.dirname(os.path.abspath(a.story)), "backdrops")
    root = tk.Tk(); App(root, a); root.mainloop()
