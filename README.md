# 🎬 AI Video to Shorts Tool — Setup Guide

## Aapko Kya Kya Milega:
- `app.py` — Python backend (video download, cut, upload)
- `index.html` — Beautiful frontend UI
- `requirements.txt` — Python packages
- `Dockerfile` — Railway deploy ke liye

---

## STEP 1 — GitHub Pe Upload Karein

1. github.com pe jaayein → New Repository banayein
   - Name: `video-shorts-tool`
   - Public select karein
   - Create Repository

2. Apne computer pe yeh files ek folder mein rakhein:
   - app.py
   - index.html
   - requirements.txt
   - Dockerfile

3. GitHub Desktop (github.com/desktop) se ya directly upload karein

---

## STEP 2 — Railway Pe Deploy Karein

1. railway.app pe jaayein
2. "New Project" → "GitHub Repository" select karein
3. Apna `video-shorts-tool` repo select karein
4. Deploy click karein

---

## STEP 3 — Environment Variables Set Karein (ZAROORI!)

Railway mein deploy hone ke baad:
1. Project settings mein jaayein
2. "Variables" tab click karein
3. Yeh variables add karein:

```
GEMINI_API_KEY      = [Aapki Gemini API Key]
YT_CLIENT_ID        = [Aapka YouTube Client ID]
YT_CLIENT_SECRET    = [Aapka YouTube Client Secret]
REDIRECT_URI        = https://[AAPKA-RAILWAY-URL]/oauth2callback
FLASK_SECRET        = koi-bhi-random-string-likhein-jaise-abc123xyz
```

---

## STEP 4 — YouTube OAuth Redirect URI Update Karein

1. console.cloud.google.com pe jaayein
2. APIs & Services → Credentials
3. Apna OAuth Client open karein
4. "Authorized redirect URIs" mein add karein:
   `https://[AAPKA-RAILWAY-URL]/oauth2callback

---

## STEP 5 — Frontend Update Karein

`index.html` mein yeh line dhundein:
```
const API_BASE = window.location.hostname === 'localhost'
  ? 'http://localhost:5000'
  : 'https://YOUR-RAILWAY-URL.railway.app';
```

`YOUR-RAILWAY-URL` ki jagah apna actual Railway URL daalo

---

## STEP 6 — index.html Ko GitHub Pages Pe Host Karein (FREE)

1. GitHub repo settings → Pages
2. Source: main branch → /root
3. Save karein
4. Aapka URL milega: `https://[username].github.io/video-shorts-tool`

---

## Use Kaise Karein:

1. index.html ka URL browser mein kholen
2. "YouTube Se Connect Karein" click karein
3. Login karein
4. Video link paste karein
5. "Preview" click karein
6. Kitni shorts chahiye select karein
7. "Shuru Karein" click karein
8. 5-15 minutes mein sab automatic ho jaayega!

---

## Support:
Koi problem aaye toh screenshot bhejein 🙏
