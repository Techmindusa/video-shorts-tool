import os
import re
import json
import time
import subprocess
import threading
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import yt_dlp

app = Flask(__name__)
CORS(app, origins="*")

# ── CONFIG ────────────────────────────────────────────────────
GEMINI_API_KEY   = os.environ.get('GEMINI_API_KEY', '')
YT_CLIENT_ID     = os.environ.get('YT_CLIENT_ID', '')
YT_CLIENT_SECRET = os.environ.get('YT_CLIENT_SECRET', '')
REDIRECT_URI     = os.environ.get('REDIRECT_URI', '')

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    model = None

# State store (session ki jagah simple dict use karo)
oauth_states = {}
jobs = {}

def get_client_config():
    return {
        'web': {
            'client_id': YT_CLIENT_ID,
            'client_secret': YT_CLIENT_SECRET,
            'redirect_uris': [REDIRECT_URI],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token'
        }
    }

# ── ROUTES ────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({'status': 'VideoTool API Running', 'version': '2.0'})

@app.route('/auth/login')
def auth_login():
    try:
        flow = Flow.from_client_config(
            get_client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        # State save karo
        oauth_states[state] = True
        return jsonify({'success': True, 'auth_url': auth_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/oauth2callback')
def oauth2callback():
    try:
        state = request.args.get('state', '')
        code  = request.args.get('code', '')
        error = request.args.get('error', '')

        if error:
            return f"<h2>❌ Error: {error}</h2>"

        flow = Flow.from_client_config(
            get_client_config(),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        # State verify mat karo (session nahi hai)
        flow.fetch_token(code=code)
        creds = flow.credentials

        creds_dict = {
            'token'        : creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri'    : 'https://oauth2.googleapis.com/token',
            'client_id'    : YT_CLIENT_ID,
            'client_secret': YT_CLIENT_SECRET,
            'scopes'       : SCOPES
        }

        creds_json = json.dumps(creds_dict)
        return f"""<!DOCTYPE html>
<html><head><title>Login Success</title></head>
<body style="background:#0a0a0f;color:#e8e8f5;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px">
<div style="font-size:48px">✅</div>
<h2 style="color:#2ed573">YouTube Connected!</h2>
<p style="color:#9090b0">Yeh window band kar sakte ho</p>
<script>
try {{
  if (window.opener) {{
    window.opener.postMessage({{type:'YT_AUTH',credentials:{creds_json}}}, '*');
  }}
}} catch(e) {{}}
setTimeout(function(){{ window.close(); }}, 2000);
</script>
</body></html>"""

    except Exception as e:
        return f"""<!DOCTYPE html>
<html><body style="background:#0a0a0f;color:#f87171;font-family:sans-serif;padding:40px">
<h2>❌ OAuth Error</h2>
<p>{str(e)}</p>
<p style="color:#9090b0">Console.cloud.google.com pe jaayein → Credentials → Redirect URI check karein</p>
</body></html>"""

@app.route('/video-info', methods=['POST', 'OPTIONS'])
def video_info():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    try:
        url = request.json.get('url', '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'URL required'})
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'success'    : True,
                'title'      : info.get('title', ''),
                'duration'   : info.get('duration', 0),
                'description': info.get('description', '')[:300],
                'uploader'   : info.get('uploader', ''),
                'thumbnail'  : info.get('thumbnail', '')
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/process', methods=['POST', 'OPTIONS'])
def process_video():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    try:
        data        = request.json
        url         = data.get('url', '').strip()
        credentials = data.get('credentials', {})
        num_clips   = min(max(int(data.get('num_clips', 1)), 1), 5)

        if not url:
            return jsonify({'success': False, 'error': 'Video URL required'})
        if not credentials:
            return jsonify({'success': False, 'error': 'YouTube login required'})

        job_id = f'job_{int(time.time() * 1000)}'
        jobs[job_id] = {
            'status'         : 'starting',
            'progress'       : 0,
            'message'        : 'Process shuru ho raha hai...',
            'video_title'    : '',
            'uploaded_videos': []
        }

        thread = threading.Thread(
            target=process_video_job,
            args=(url, credentials, num_clips, job_id),
            daemon=True
        )
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/status/<job_id>')
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({'success': False, 'error': 'Job not found'})
    return jsonify({'success': True, **jobs[job_id]})

# ── VIDEO PROCESSING ──────────────────────────────────────────

def process_video_job(url, credentials_dict, num_clips, job_id):
    try:
        base_dir = f'/tmp/vt_{job_id}'
        os.makedirs(base_dir, exist_ok=True)

        # Step 1: Info fetch
        jobs[job_id].update({'status':'fetching','progress':5,'message':'Video info fetch ho rahi hai...'})
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_info = {
            'title'      : info.get('title', ''),
            'description': info.get('description', '')[:500],
            'uploader'   : info.get('uploader', '')
        }
        jobs[job_id]['video_title'] = video_info['title']

        # Step 2: Download
        jobs[job_id].update({'status':'downloading','progress':10,'message':'Video download ho rahi hai...'})
        input_path = f'{base_dir}/original.mp4'

        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    pct = float(d.get('_percent_str','0%').strip().replace('%',''))
                    jobs[job_id]['progress'] = 10 + int(pct * 0.35)
                except: pass

        dl_opts = {
            'format'            : 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            'outtmpl'           : input_path,
            'merge_output_format': 'mp4',
            'progress_hooks'    : [progress_hook],
            'quiet'             : True
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])

        # Step 3: Duration
        jobs[job_id].update({'progress':46,'message':'Video analyze ho rahi hai...'})
        result = subprocess.run(
            ['ffprobe','-v','error','-show_entries','format=duration',
             '-of','default=noprint_wrappers=1:nokey=1', input_path],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())

        # Step 4: Clips banao
        clip_dur = min(55, int(duration / num_clips))
        positions = [0.1, 0.35, 0.58, 0.72, 0.85]
        uploaded_videos = []

        for i in range(num_clips):
            base_pct = 46 + i * (50 // num_clips)

            # Cut
            jobs[job_id].update({'progress': base_pct+2, 'message': f'Clip {i+1} cut ho rahi hai...'})
            start = int(duration * positions[i])
            out_path = f'{base_dir}/short_{i+1}.mp4'

            subprocess.run([
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', input_path,
                '-t', str(clip_dur),
                '-vf', 'crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
                '-c:v', 'libx264', '-c:a', 'aac',
                '-b:v', '2M', '-b:a', '128k', '-r', '30',
                '-movflags', '+faststart',
                out_path
            ], capture_output=True)

            # AI Metadata
            jobs[job_id].update({'progress': base_pct+8, 'message': f'Clip {i+1} ke liye AI title ban raha hai...'})
            metadata = generate_metadata(video_info, i+1, num_clips)

            # Upload
            jobs[job_id].update({'progress': base_pct+14, 'message': f'Clip {i+1} YouTube pe upload ho rahi hai...'})
            video_id = upload_youtube(out_path, metadata, credentials_dict)

            uploaded_videos.append({
                'clip_num'   : i+1,
                'youtube_id' : video_id,
                'youtube_url': f'https://youtube.com/shorts/{video_id}',
                'title'      : metadata['title'],
                'hashtags'   : metadata['hashtags']
            })

            try: os.remove(out_path)
            except: pass

        # Cleanup
        try:
            import shutil
            shutil.rmtree(base_dir)
        except: pass

        jobs[job_id].update({
            'status'         : 'completed',
            'progress'       : 100,
            'message'        : f'✅ {num_clips} shorts successfully upload ho gayi!',
            'uploaded_videos': uploaded_videos
        })

    except Exception as e:
        jobs[job_id].update({'status':'error','message':f'Error: {str(e)}','progress':0})

def generate_metadata(video_info, clip_num, total):
    if not model:
        return {
            'title'   : f"Amazing Short #{clip_num} | {video_info['title'][:40]}",
            'description': f"Watch this! {video_info['title']}\n\n#Shorts #Viral #Trending",
            'tags'    : ['shorts','viral','trending','youtube'],
            'hashtags': '#Shorts #Viral #Trending #YouTube'
        }
    try:
        prompt = f"""YouTube Shorts expert ho tum. Is video ke clip {clip_num} ke liye metadata banao.

Video: {video_info['title']}
Channel: {video_info['uploader']}

JSON format mein return karo (sirf JSON, kuch aur nahi):
{{
  "title": "catchy title max 90 chars",
  "description": "engaging description 150 words emojis ke saath",
  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"],
  "hashtags": "#Shorts #Viral #tag1 #tag2 #tag3"
}}"""
        resp = model.generate_content(prompt)
        text = resp.text.strip()
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except: pass

    return {
        'title'      : f"Must Watch Clip #{clip_num} | {video_info['title'][:40]}",
        'description': f"Amazing content!\n\n{video_info['title']}\n\n#Shorts #Viral",
        'tags'       : ['shorts','viral','trending'],
        'hashtags'   : '#Shorts #Viral #Trending'
    }

def upload_youtube(video_path, metadata, creds_dict):
    creds = Credentials(
        token         = creds_dict.get('token'),
        refresh_token = creds_dict.get('refresh_token'),
        token_uri     = 'https://oauth2.googleapis.com/token',
        client_id     = YT_CLIENT_ID,
        client_secret = YT_CLIENT_SECRET,
        scopes        = SCOPES
    )
    youtube = build('youtube', 'v3', credentials=creds)
    body = {
        'snippet': {
            'title'      : metadata['title'],
            'description': metadata['description'] + '\n\n' + metadata['hashtags'] + '\n\n#Shorts',
            'tags'       : metadata['tags'] + ['shorts','youtubeshorts'],
            'categoryId' : '22'
        },
        'status': {
            'privacyStatus'           : 'public',
            'selfDeclaredMadeForKids' : False
        }
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
    req = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        _, response = req.next_chunk()
    return response.get('id', '')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
