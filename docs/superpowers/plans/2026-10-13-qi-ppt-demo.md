# 启真问智比赛 PPT 与 Demo 视频 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an anonymized, review-ready PPT and a 5–6 minute MP4 demo showing the real MedChat teaching workflow.

**Architecture:** Copy the supplied deck into `deliverables/qi/`, preserve its slide master and 16:9 layout, then edit only content and metadata needed for competition delivery. Run MedChat locally on port 6001, capture the reliable target-search, molecular-design, and ADMET pages, and assemble a narrated 16:9 video with a truthful fallback scene if activity or docking dependencies are unavailable.

**Tech Stack:** Existing PPTX, PowerPoint/LibreOffice or artifact-tool for rendering, FastAPI/Uvicorn, browser capture, FFmpeg, HyperFrames or an equivalent deterministic HTML timeline, and a local Chinese TTS voice if available.

---

### Task 1: Stage the deliverables and inspect the source deck

**Files:**
- Create: `deliverables/qi/MedChat_启真问智_作品说明_交付版.pptx`
- Create: `deliverables/qi/` working assets and manifests
- Source: `C:\Users\xkx52\Desktop\qi\MedChat_启真问智_作品说明_视觉升级版.pptx`

- [ ] **Step 1: Copy the source deck without editing the original.**

Run:
```powershell
New-Item -ItemType Directory -Force deliverables/qi | Out-Null
Copy-Item 'C:\Users\xkx52\Desktop\qi\MedChat_启真问智_作品说明_视觉升级版.pptx' 'deliverables/qi/MedChat_启真问智_作品说明_交付版.pptx'
```

- [ ] **Step 2: Inspect slide count, dimensions, text, images, notes, and core metadata.**

Expected: 20 slides, 16:9 layout, blue-white template, editable text and existing screenshots; record any school/person identifiers for redaction.

- [ ] **Step 3: Render the copied deck before edits.**

Expected: a readable contact sheet or per-slide PNGs for later side-by-side comparison.

---

### Task 2: Edit the deck for competition delivery

**Files:**
- Modify: `deliverables/qi/MedChat_启真问智_作品说明_交付版.pptx`
- Create: `deliverables/qi/ppt_anonymization_check.json`

- [ ] **Step 1: Replace the project framing with the education-agent title and fill the actual track.**

Use one consistent project name across title, section labels, and closing slide. Set the track label to the confirmed track(s), or leave a clearly marked review item if the organizer has not confirmed it.

- [ ] **Step 2: Tighten slide copy without adding unsupported claims.**

Keep the workflow sequence “查找依据 → 设计候选 → 计算评价 → 解释结果”. Replace generic technology prose with short descriptions of teacher action, student action, evidence returned, and learning output.

- [ ] **Step 3: Remove or anonymize school/person identifiers.**

Redact visible school/person text, QR destinations that expose identity, screenshots with institutional headers, and core properties such as creator and lastModifiedBy. Do not alter scientific figures or tool provenance.

- [ ] **Step 4: Validate slide fit and editability.**

Render all slides and inspect titles, body text, screenshots, footers, and page numbers. Fix overflow by shortening copy or adjusting layout within the existing master.

- [ ] **Step 5: Export a PDF copy for the submission-size check.**

Expected: PDF opens, remains under 50 MB, and matches the final PPT visually.

---

### Task 3: Prepare and run the truthful local demo

**Files:**
- Create: `deliverables/qi/demo/README.md`
- Create: `deliverables/qi/demo/run_manifest.json`
- Do not modify: existing source code, model files, runtime databases, or user changes

- [ ] **Step 1: Start MedChat on the browser-safe port.**

Run:
```powershell
python main.py --port 6001
```

Set only runtime variables required to select the approved activity bundle; do not change registry files during recording.

- [ ] **Step 2: Preflight the deterministic demo pages.**

Check target search for PDE5A, molecular design with `c1cc([*])ccc1`, and ADMET with a built-in example. Record status, provenance, warnings, and unavailable states. Run docking/activity only when their real environment checks pass.

- [ ] **Step 3: Capture a 5–6 minute sequence.**

Sequence: opening teaching task, target evidence, candidate design and RDKit properties, ADMET provenance, activity/docking if available, and closing limitations. Avoid save/export controls that write runtime assets.

- [ ] **Step 4: Keep identity neutral in the recording.**

Crop or mask institutional headers and personal details in the browser frame while preserving tool names, model versions, warnings, and scientific outputs.

---

### Task 4: Assemble narration, subtitles, and video

**Files:**
- Create: `deliverables/qi/demo/storyboard.md`
- Create: `deliverables/qi/demo/narration_zh.txt`
- Create: `deliverables/qi/demo/captions.srt`
- Create: `deliverables/qi/MedChat_启真问智_演示Demo.mp4`

- [ ] **Step 1: Write a time-coded narration of roughly 750–900 Chinese characters.**

Use clear explanations of what the learner does, what the agent/tool returns, and what evidence remains to be checked. Do not claim measured learning gains or experimental validation.

- [ ] **Step 2: Generate or record the narration and align captions.**

Use a natural Chinese voice, leave short pauses between workflow stages, and keep on-screen captions large enough for a 16:9 export.

- [ ] **Step 3: Assemble the timeline.**

Use real browser captures as the main footage, add a short title card and end card, and use simple cuts or restrained crossfades. If a scientific dependency is unavailable, show the actual unavailable message and explain the boundary.

- [ ] **Step 4: Render MP4.**

Expected: H.264 MP4, 1920×1080 or 1280×720, 25–30 fps, total duration 5–6 minutes, with synchronized audio and subtitles.

---

### Task 5: Final verification and delivery

**Files:**
- Modify: `deliverables/qi/ppt_anonymization_check.json`
- Create: `deliverables/qi/delivery_manifest.json`

- [ ] **Step 1: Verify the PPT and PDF.**

Check that every slide opens, text is readable, the track is filled, no identity metadata remains, and the PDF is under 50 MB.

- [ ] **Step 2: Verify the MP4.**

Use FFprobe or equivalent to check duration, video dimensions, codec, audio presence, and subtitle readability at representative frames.

- [ ] **Step 3: Verify scientific truthfulness.**

Confirm that every numeric result shown came from a real local model/tool response or is explicitly labeled as unavailable/demo illustration. Confirm no local secrets, personal data, model caches, or runtime databases are packaged.

- [ ] **Step 4: Deliver only the final PPT and MP4.**

Return direct file links to the final PPTX and MP4 and mention the PDF only if useful for submission.
