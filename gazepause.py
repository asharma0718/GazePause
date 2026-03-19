"""
GazePause — Single window, embedded webcam, dropdown action selector.
Compatible with mediapipe 0.10.x+
"""

import cv2
import pyautogui
import tkinter as tk
from tkinter import ttk
import threading
import time
import sys
from collections import deque

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# ─── CONFIG ──────────────────────────────────────────────────────────────────
CAMERA_INDEX     = 0
PAUSE_DELAY_SEC  = 1.5
RESUME_DELAY_SEC = 0.5
YAW_THRESHOLD    = 14.0
PITCH_THRESHOLD  = 14.0
SMOOTHING_FRAMES = 6
PROCESS_EVERY_N  = 2
CAM_W, CAM_H     = 320, 240
# ─────────────────────────────────────────────────────────────────────────────

import mediapipe as mp

# Fix Windows DPI scaling making window huge
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

try:
    _legacy = mp.solutions.face_mesh
    LEGACY_API = True
except AttributeError:
    LEGACY_API = False

NOSE_TIP     = 1
LEFT_EYE_IN  = 133
RIGHT_EYE_IN = 362
FOREHEAD     = 10
CHIN         = 152
LEFT_IRIS    = 468
RIGHT_IRIS   = 473

BG      = "#0f1110"
SURFACE = "#181c1a"
SURF2   = "#1e2420"
BORDER  = "#252b27"
GREEN   = "#a8f072"
GREEN2  = "#2a3d22"
RED     = "#f07272"
RED2    = "#3d2222"
AMBER   = "#f0c472"
MUTED   = "#4a5c4e"
TEXT    = "#d8e8d8"


def get_landmark_list(face_result):
    if LEGACY_API:
        if not face_result or not face_result.multi_face_landmarks:
            return None
        return face_result.multi_face_landmarks[0].landmark
    else:
        if not face_result or not face_result.face_landmarks:
            return None
        return face_result.face_landmarks[0]


def compute_gaze(landmarks):
    lm    = landmarks
    nose  = lm[NOSE_TIP]
    l_eye = lm[LEFT_EYE_IN]
    r_eye = lm[RIGHT_EYE_IN]
    fore  = lm[FOREHEAD]
    chin_ = lm[CHIN]

    eye_mid_x  = (l_eye.x + r_eye.x) / 2
    eye_width  = abs(l_eye.x - r_eye.x) + 1e-6
    yaw_deg    = ((nose.x - eye_mid_x) / eye_width) * 90.0
    face_mid_y  = (fore.y + chin_.y) / 2
    face_height = abs(chin_.y - fore.y) + 1e-6
    pitch_deg   = ((nose.y - face_mid_y) / face_height) * 90.0

    iris_score = 1.0
    try:
        l_iris     = lm[LEFT_IRIS]
        r_iris     = lm[RIGHT_IRIS]
        iris_mid_x = (l_iris.x + r_iris.x) / 2
        iris_score = max(0.0, 1.0 - abs(iris_mid_x - eye_mid_x) / eye_width * 3.0)
    except (IndexError, AttributeError):
        pass

    yaw_conf   = max(0.0, 1.0 - abs(yaw_deg)   / YAW_THRESHOLD)
    pitch_conf = max(0.0, 1.0 - abs(pitch_deg) / PITCH_THRESHOLD)
    confidence = yaw_conf * 0.5 + pitch_conf * 0.3 + iris_score * 0.2
    looking = (abs(yaw_deg) < YAW_THRESHOLD and
               abs(pitch_deg) < PITCH_THRESHOLD and
               confidence > 0.35)
    return looking, confidence


class GazePause:
    def __init__(self):
        self.running      = False
        self.looking_away = False
        self.away_since   = None
        self.back_since   = None
        self.paused_by_us = False
        self.pause_count  = 0
        self.frame_count  = 0
        self.last_result  = None
        self.conf_q       = deque(maxlen=SMOOTHING_FRAMES)
        self.status       = "idle"
        self.gaze_pct     = 0
        self.watch_sec    = 0
        self.away_sec     = 0
        self.action       = "space"
        self.on_frame     = None
        self.on_status_change = None

    def set_action(self, action):
        self.action = action

    def run(self):
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            self.status = "error"
            self._notify()
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        self.running = True
        self.status  = "watching"
        self._notify()
        if LEGACY_API:
            self._run_legacy(cap)
        else:
            self._run_new(cap)
        cap.release()

    def _run_legacy(self, cap):
        with mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True,
            min_detection_confidence=0.5, min_tracking_confidence=0.5,
        ) as face_mesh:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue
                self._process_frame(frame, face_mesh, legacy=True)
                time.sleep(0.005)

    def _run_new(self, cap):
        import urllib.request, os, tempfile
        model_path = os.path.join(tempfile.gettempdir(), "face_landmarker.task")
        if not os.path.exists(model_path):
            print("[GazePause] Downloading model (~6MB)...")
            url = ("https://storage.googleapis.com/mediapipe-models/"
                   "face_landmarker/face_landmarker/float16/1/face_landmarker.task")
            urllib.request.urlretrieve(url, model_path)
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core import base_options as bo
        options = vision.FaceLandmarkerOptions(
            base_options=bo.BaseOptions(model_asset_path=model_path),
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=1,
        )
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue
                self._process_frame(frame, landmarker, legacy=False)
                time.sleep(0.005)

    def _process_frame(self, frame, detector, legacy):
        self.frame_count += 1
        now = time.time()
        if self.frame_count % PROCESS_EVERY_N == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if legacy:
                rgb.flags.writeable = False
                result = detector.process(rgb)
            else:
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = detector.detect(mp_img)
            self.last_result = result
        else:
            result = self.last_result

        lm_list = get_landmark_list(result)
        if lm_list:
            looking, conf = compute_gaze(lm_list)
            self.conf_q.append(conf)
            self.gaze_pct = int(sum(self.conf_q) / len(self.conf_q) * 100)
            if looking:
                self._handle_looking(now)
            else:
                self._handle_away(now)
        else:
            self.conf_q.append(0)
            self.gaze_pct = 0
            looking = False
            self._handle_away(now)

        if not self.looking_away:
            self.watch_sec += PROCESS_EVERY_N / 30.0
        else:
            self.away_sec  += PROCESS_EVERY_N / 30.0

        if self.on_frame:
            display = cv2.flip(frame, 1)
            display = cv2.resize(display, (CAM_W, CAM_H))
            rgb_out = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
            self.on_frame(rgb_out, looking, self.gaze_pct)

    def _handle_looking(self, now):
        if self.looking_away:
            if self.back_since is None:
                self.back_since = now
            elif now - self.back_since >= RESUME_DELAY_SEC:
                self.looking_away = False
                self.away_since   = None
                self.back_since   = None
                if self.paused_by_us:
                    self._do_action("RESUME")
                    self.paused_by_us = False
                self.status = "watching"
                self._notify()
        else:
            self.back_since = None

    def _handle_away(self, now):
        self.back_since = None
        if not self.looking_away:
            self.looking_away = True
            self.away_since   = now
            self.status       = "away"
            self._notify()
        else:
            if now - self.away_since >= PAUSE_DELAY_SEC and not self.paused_by_us:
                self._do_action("PAUSE")
                self.paused_by_us = True
                self.pause_count += 1
                self.status       = "paused"
                self._notify()

    def _do_action(self, reason):
        print(f"[GazePause] {reason} -> {self.action}")
        try:
            if self.action == "space":
                pyautogui.press('space')
            elif self.action == "left_click":
                pyautogui.click(button='left')
            elif self.action == "right_click":
                pyautogui.click(button='right')
        except Exception as e:
            print(f"[GazePause] Error: {e}")

    def _notify(self):
        if self.on_status_change:
            self.on_status_change(self.status, self.gaze_pct,
                                  self.pause_count, self.watch_sec, self.away_sec)

    def stop(self):
        self.running = False


class App:
    ACTION_OPTIONS = {
        "Spacebar — YouTube, Netflix, VLC…": "space",
        "Left click":                        "left_click",
        "Right click":                       "right_click",
    }
    STATUS_CFG = {
        "idle":     (MUTED,  "Idle"),
        "watching": (GREEN,  "● Watching"),
        "away":     (AMBER,  "◐ Looking away…"),
        "paused":   (RED,    "■ Paused by gaze"),
        "error":    (RED,    "✕ Camera error"),
    }

    def __init__(self):
        self.gaze    = GazePause()
        self.running = False
        self._photo  = None

        self.root = tk.Tk()
        self.root.title("GazePause")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        # Fixed size window
        self.root.geometry("560x420+100+100")
        self.root.update_idletasks()
        self.root.minsize(560, 420)
        self.root.maxsize(560, 420)

        self._build_ui()
        self.gaze.on_frame         = self._on_frame
        self.gaze.on_status_change = self._on_status

    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self.root, bg=SURFACE, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="GazePause",
                 font=("Helvetica", 15, "bold"),
                 fg=GREEN, bg=SURFACE).pack(side="left", padx=14)
        tk.Label(hdr, text="Fires an action when you look away",
                 font=("Helvetica", 9), fg=MUTED, bg=SURFACE).pack(side="left")

        # ── Body ──
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        # ── Left: camera ──
        cam_frame = tk.Frame(body, bg=BORDER, width=CAM_W+4, height=CAM_H+4)
        cam_frame.pack(side="left", fill="none", expand=False)
        cam_frame.pack_propagate(False)

        self.cam_label = tk.Label(cam_frame, bg="#000000",
                                  width=CAM_W, height=CAM_H)
        self.cam_label.place(x=2, y=2)

        self.cam_placeholder = tk.Label(cam_frame,
                                        text="Camera\nnot started",
                                        font=("Helvetica", 10),
                                        fg=MUTED, bg="#000000")
        self.cam_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # ── Right: controls ──
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # Status
        self.status_label = tk.Label(right, text="Idle",
                                     font=("Helvetica", 14, "bold"),
                                     fg=MUTED, bg=BG, anchor="w")
        self.status_label.pack(fill="x", pady=(0, 6))

        # Confidence bar
        tk.Label(right, text="Gaze confidence",
                 font=("Helvetica", 8), fg=MUTED, bg=BG,
                 anchor="w").pack(fill="x")
        self.bar_canvas = tk.Canvas(right, height=6, bg=SURF2,
                                    highlightthickness=0)
        self.bar_canvas.pack(fill="x", pady=(2, 10))
        self.bar_rect = self.bar_canvas.create_rectangle(
            0, 0, 0, 6, fill=GREEN, outline="")

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # Action dropdown
        tk.Label(right, text="Action on look-away",
                 font=("Helvetica", 8), fg=MUTED, bg=BG,
                 anchor="w").pack(fill="x")

        self.action_var = tk.StringVar(value="Spacebar — YouTube, Netflix, VLC…")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("G.TCombobox",
                        fieldbackground=SURF2, background=SURF2,
                        foreground=TEXT, selectbackground=SURF2,
                        selectforeground=TEXT, bordercolor=BORDER,
                        arrowcolor=GREEN)
        style.map("G.TCombobox",
                  fieldbackground=[("readonly", SURF2)],
                  foreground=[("readonly", TEXT)],
                  background=[("readonly", SURF2)])
        self.dropdown = ttk.Combobox(
            right, textvariable=self.action_var,
            values=list(self.ACTION_OPTIONS.keys()),
            state="readonly", style="G.TCombobox",
            font=("Helvetica", 9), width=28)
        self.dropdown.pack(anchor="w", pady=(2, 8))
        self.dropdown.bind("<<ComboboxSelected>>", self._on_action_change)

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # Stats row
        stats = tk.Frame(right, bg=BG)
        stats.pack(fill="x", pady=(0, 8))
        self._stat_pauses = self._stat(stats, "Pauses", "0",  0)
        self._stat_watch  = self._stat(stats, "Watch",  "0:00", 1)
        self._stat_attn   = self._stat(stats, "Attn",   "--",  2)

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # Buttons
        btns = tk.Frame(right, bg=BG)
        btns.pack(anchor="w")
        self.toggle_btn = tk.Button(btns, text="Start",
                                    font=("Helvetica", 10, "bold"),
                                    fg=GREEN, bg=GREEN2,
                                    activebackground=GREEN2, activeforeground=GREEN,
                                    relief="flat", padx=16, pady=5,
                                    cursor="hand2", bd=0, command=self.toggle)
        self.toggle_btn.pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Quit",
                  font=("Helvetica", 10), fg=MUTED, bg=SURF2,
                  activebackground=SURF2, activeforeground=TEXT,
                  relief="flat", padx=16, pady=5,
                  cursor="hand2", bd=0, command=self.quit).pack(side="left")

    def _stat(self, parent, label, val, col):
        f = tk.Frame(parent, bg=SURFACE, padx=8, pady=5)
        f.grid(row=0, column=col, padx=(0, 6))
        tk.Label(f, text=label, font=("Helvetica", 7), fg=MUTED, bg=SURFACE).pack()
        v = tk.Label(f, text=val, font=("Helvetica", 12, "bold"), fg=TEXT, bg=SURFACE)
        v.pack()
        return v

    def _on_frame(self, rgb, looking, gaze_pct):
        from PIL import Image, ImageTk
        img          = Image.fromarray(rgb)
        border_color = (168, 240, 114) if looking else (240, 114, 114)
        bordered     = Image.new("RGB", (CAM_W + 4, CAM_H + 4), border_color)
        bordered.paste(img, (2, 2))
        photo = ImageTk.PhotoImage(bordered)
        self.root.after(0, self._set_cam, photo)

    def _set_cam(self, photo):
        self._photo = photo
        self.cam_label.config(image=photo)
        self.cam_placeholder.place_forget()

    def _on_status(self, status, gaze_pct, pause_count, watch_sec, away_sec):
        self.root.after(0, self._update_ui,
                        status, gaze_pct, pause_count, watch_sec, away_sec)

    def _update_ui(self, status, gaze_pct, pause_count, watch_sec, away_sec):
        fg, text = self.STATUS_CFG.get(status, (MUTED, "Idle"))
        self.status_label.config(text=text, fg=fg)

        self.bar_canvas.update_idletasks()
        w      = self.bar_canvas.winfo_width()
        fill_w = int(w * gaze_pct / 100)
        color  = GREEN if gaze_pct > 60 else AMBER if gaze_pct > 30 else RED
        self.bar_canvas.coords(self.bar_rect, 0, 0, fill_w, 6)
        self.bar_canvas.itemconfig(self.bar_rect, fill=color)

        def fmt(s):
            return f"{int(s)//60}:{int(s)%60:02d}"
        total = watch_sec + away_sec
        attn  = f"{int(watch_sec/total*100)}%" if total > 0 else "--"
        self._stat_pauses.config(text=str(pause_count))
        self._stat_watch.config(text=fmt(watch_sec))
        self._stat_attn.config(text=attn)

    def _on_action_change(self, _=None):
        self.gaze.set_action(self.ACTION_OPTIONS[self.action_var.get()])

    def toggle(self):
        if self.running:
            self.gaze.stop()
            self.toggle_btn.config(text="Start", fg=GREEN, bg=GREEN2)
            self.running = False
            self.cam_placeholder.place(relx=0.5, rely=0.5, anchor="center")
            self.cam_label.config(image="")
            self._photo = None
        else:
            self.running = True
            self.toggle_btn.config(text="Stop", fg=RED, bg=RED2)
            self.gaze.running = False
            self.gaze.looking_away = False
            self.gaze.away_since = None
            self.gaze.back_since = None
            self.gaze.paused_by_us = False
            self.gaze.pause_count = 0
            self.gaze.frame_count = 0
            self.gaze.watch_sec = 0
            self.gaze.away_sec = 0
            self.gaze.conf_q.clear()
            threading.Thread(target=self.gaze.run, daemon=True).start()

    def quit(self):
        self.gaze.stop()
        self.root.destroy()
        sys.exit(0)

    def start(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        from PIL import Image, ImageTk
    except ImportError:
        print("[GazePause] Installing Pillow...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
        from PIL import Image, ImageTk

    print("=" * 50)
    print("  GazePause - action fires when you look away")
    print(f"  MediaPipe API: {'legacy' if LEGACY_API else 'new (0.10.x+)'}")
    print("=" * 50)

    app = App()
    app.start()
