# GazePause
Pauses and resumes any video by pressing spacebar when you look away or back at the screen, using webcam gaze detection

Works with any video player, any website (YouTube, Netflix, etc.), or any app — because it sends a real keypress or click to whatever window is in focus.

---

## Requirements

- Windows 10/11
- Python 3.8+
- A webcam

---

## Installation

**Step 1 — Install Python**

Download from https://python.org/downloads and run the installer.
Check **"Add Python to PATH"** on the first screen before clicking Install.

**Step 2 — Install dependencies**

Open PowerShell and run:

```powershell
pip install opencv-python mediapipe pyautogui Pillow
```

---

## Running

```powershell
python "C:\path\to\gazepause.py"
```

Then click **Start** in the window to begin gaze detection.

---

## How it works

1. Your webcam feed is processed locally using **MediaPipe Face Mesh**
2. Head pose (yaw + pitch) and iris position are tracked in real time
3. If you look away for 1.5 seconds → spacebar fires → video pauses
4. When you look back for 0.5 seconds → spacebar fires again → video resumes

---

## Settings

| Setting | Default | What it does |
|---|---|---|
| `PAUSE_DELAY_SEC` | 1.5 | Seconds looking away before action fires |
| `RESUME_DELAY_SEC` | 0.5 | Seconds looking back before action fires |
| `YAW_THRESHOLD` | 14.0 | Degrees of left/right head turn before "away" |
| `PITCH_THRESHOLD` | 14.0 | Degrees of up/down head tilt before "away" |
| `CAMERA_INDEX` | 0 | Which camera to use (try 1 or 2 if wrong one opens) |

These are at the top of `gazepause.py` and can be edited in any text editor.

---

## Action options

Use the dropdown in the UI to choose what fires when you look away:

- **Spacebar** — works with YouTube, Netflix, VLC, most video players
- **Left click** — for sites that use click to pause
- **Right click** — for custom setups

---

## Privacy

- All processing happens locally on your machine
- No video data is ever transmitted anywhere
- MediaPipe runs fully on-device
- No files are written to disk
- Camera is released when you stop or quit

---

## Emergency stop

Move your mouse to the **top-left corner** of your screen to immediately kill the process (pyautogui failsafe).

---

## Troubleshooting

**Window opens but is too small / buttons missing**
Your display scaling may be affecting the layout. Go to Windows Settings → Display → Scale and check what it's set to.

**Wrong camera opens**
Change `CAMERA_INDEX = 0` to `1` or `2` at the top of the script.

**Spacebar not working on a specific site**
Try switching the action dropdown to **Left click** instead.

**"No face detected" showing constantly**
Make sure your face is well lit and you're sitting within ~1 metre of the camera.
