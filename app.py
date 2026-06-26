import os
import re
import json
import time
import subprocess
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import yt_dlp

app = Flask(__name__)
CORS(app, origins="*")

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

jobs = {}

def get_client_config():
    return {
        'web': {
            'client_id'    : YT_CLIENT_ID,
            'client_secret': YT_CLIENT_SECRET,
            'redirect_uris': [REDIRECT_URI],
            'auth_uri'     : 'https://accounts.google.com/o/oauth2/auth',
            'token_uri'    : 'https://oauth2.googleapis.com/token'
        }
    }

# ── YT-DLP OPTIONS (Bot detection bypass) ─────────────────────
def get_ydl_opts(extra=None):
    """YouTube bot detection bypass karne ke liye options"""
    opts = {
        'quiet'       : True,
        'no_warnings' : True,
        # Real browser jaisa dikhao
        'http_headers': {
            'User-Agent'     : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept'         : 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
        # Cookie file use karo agar hai
        'cookiefile'  : '/tmp/cookies.txt' if os.path.exists('/tmp/cookies.txt') else None,
        # Po token bypass
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
        # Retry karo
        'retries'     : 3,
        'fragment_retries': 3,
    }
    # None values hata do
    opts = {k: v for k, v in opts.items() if v is not None}
    if extra:
        opts.update(extra)
    return opts

# ── ROUTES ────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({'status': 'VideoTool API Running', 'version': '3.0'})

@app.route('/auth/login')
def auth_login():
    try:
        flow = Flow.from_client_config(
            get_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
        )
        auth_url, state = flow.authorization_url(
            access_type='offline', prompt='consent'
        )
        return jsonify({'success': True, 'auth_url': auth_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/oauth2callback')
def oauth2callback():
    try:
        code  = request.args.get('code', '')
        error = request.args.get('error', '')
        if error:
            return f"<h2>Error: {error}</h2>"
        flow = Flow.from_client_config(
            get_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
        )
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
<html><head><title>Connected!</title></head>
<body style="background:#0a0a0f;color:#e8e8f5;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px;margin:0">
<div style="font-size:56px">✅</div>
<h2 style="color:#2ed573;margin:0">YouTube Connected!</h2>
<p style="color:#9090b0">Yeh window band ho jaayegi...</p>
<script>
try {{
  if(window.opener) {{
    window.opener.postMessage({{type:'YT_AUTH',credentials:{creds_json}}},'*');
  }}
}} catch(e){{}}
setTimeout(function(){{window.close();}},2500);
</script>
</body></html>"""
    except Exception as e:
        return f"<html><body style='background:#111;color:#f87171;padding:40px;font-family:sans-serif'><h2>Error</h2><p>{str(e)}</p></body></html>"

@app.route('/video-info', methods=['POST','OPTIONS'])
def video_info():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    try:
        url = request.json.get('url','').strip()
        if not url:
            return jsonify({'success':False,'error':'URL required'})

        opts = get_ydl_opts()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return jsonify({
            'success'    : True,
            'title'      : info.get('title',''),
            'duration'   : info.get('duration', 0),
            'description': (info.get('description','') or '')[:300],
            'uploader'   : info.get('uploader',''),
            'thumbnail'  : info.get('thumbnail','')
        })
    except Exception as e:
        err = str(e)
        # Bot detection error ke liye friendly message
        if 'Sign in to confirm' in err or 'bot' in err.lower():
            return jsonify({
                'success': False,
                'error'  : 'YouTube ne bot ki tarah block kiya. Doosri video try karein ya kuch der baad try karein.'
            })
        return jsonify({'success':False,'error': err})

@app.route('/process', methods=['POST','OPTIONS'])
def process_video():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    try:
        data        = request.json
        url         = data.get('url','').strip()
        credentials = data.get('credentials',{})
        num_clips   = min(max(int(data.get('num_clips',1)),1),5)

        if not url:
            return jsonify({'success':False,'error':'Video URL required'})
        if not credentials:
            return jsonify({'success':False,'error':'YouTube login required'})

        job_id = f'job_{int(time.time()*1000)}'
        jobs[job_id] = {
            'status'         : 'starting',
            'progress'       : 0,
            'message'        : 'Process shuru ho raha hai...',
            'video_title'    : '',
            'uploaded_videos': []
        }

        t = threading.Thread(
            target=process_video_job,
            args=(url, credentials, num_clips, job_id),
            daemon=True
        )
        t.start()
        return jsonify({'success':True,'job_id':job_id})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)})

@app.route('/status/<job_id>')
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({'success':False,'error':'Job not found'})
    return jsonify({'success':True, **jobs[job_id]})

# ── VIDEO PROCESSING ──────────────────────────────────────────

def process_video_job(url, credentials_dict, num_clips, job_id):
    try:
        base_dir = f'/tmp/vt_{job_id}'
        os.makedirs(base_dir, exist_ok=True)

        # Step 1: Info
        jobs[job_id].update({'status':'fetching','progress':5,'message':'Video info mil rahi hai...'})
        opts = get_ydl_opts()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        video_info = {
            'title'      : info.get('title',''),
            'description': (info.get('description','') or '')[:400],
            'uploader'   : info.get('uploader','')
        }
        jobs[job_id]['video_title'] = video_info['title']

        # Step 2: Download
        jobs[job_id].update({'status':'downloading','progress':10,'message':'Video download ho rahi hai... (thoda time lagega)'})
        input_path = f'{base_dir}/original.%(ext)s'

        def hook(d):
            if d['status'] == 'downloading':
                try:
                    pct = float(d.get('_percent_str','0%').strip().replace('%',''))
                    jobs[job_id]['progress'] = 10 + int(pct * 0.3)
                    jobs[job_id]['message'] = f'Download: {d.get("_percent_str","").strip()} - {d.get("_speed_str","")}'
                except: pass

        dl_opts = get_ydl_opts({
            'format'             : 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]',
            'outtmpl'            : input_path,
            'merge_output_format': 'mp4',
            'progress_hooks'     : [hook],
        })

        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])

        # Downloaded file dhundo
        actual_file = None
        for f in os.listdir(base_dir):
            if f.startswith('original'):
                actual_file = os.path.join(base_dir, f)
                break

        if not actual_file:
            raise Exception('Video download nahi hui')

        # Step 3: Duration
        jobs[job_id].update({'progress':42,'message':'Video analyze ho rahi hai...'})
        result = subprocess.run(
            ['ffprobe','-v','error','-show_entries','format=duration',
             '-of','default=noprint_wrappers=1:nokey=1', actual_file],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())
        clip_dur = min(55, max(20, int(duration / (num_clips + 1))))

        positions = [0.08, 0.25, 0.45, 0.65, 0.80]
        uploaded_videos = []

        for i in range(num_clips):
            base_pct = 42 + i * (50 // num_clips)

            # Cut karo
            jobs[job_id].update({'progress':base_pct+3,'message':f'Short #{i+1} cut ho rahi hai...'})
            start    = int(duration * positions[i])
            out_path = f'{base_dir}/short_{i+1}.mp4'

            subprocess.run([
                'ffmpeg','-y',
                '-ss', str(start),
                '-i', actual_file,
                '-t', str(clip_dur),
                '-vf', (
                    'crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),'
                    'scale=1080:1920:force_original_aspect_ratio=decrease,'
                    'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black'
                ),
                '-c:v','libx264','-c:a','aac',
                '-b:v','2M','-b:a','128k',
                '-r','30','-movflags','+faststart',
                out_path
            ], capture_output=True)

            # AI Metadata
            jobs[job_id].update({'progress':base_pct+10,'message':f'Clip #{i+1} ke liye AI title/description...'})
            metadata = generate_metadata(video_info, i+1, num_clips)

            # Upload
            jobs[job_id].update({'progress':base_pct+16,'message':f'Clip #{i+1} YouTube pe upload ho rahi hai...'})
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

        try:
            import shutil
            shutil.rmtree(base_dir)
        except: pass

        jobs[job_id].update({
            'status'         : 'completed',
            'progress'       : 100,
            'message'        : f'🎉 {num_clips} shorts YouTube pe upload ho gayi!',
            'uploaded_videos': uploaded_videos
        })

    except Exception as e:
        err = str(e)
        if 'Sign in to confirm' in err or 'bot' in err.lower():
            err = 'YouTube ne bot ki tarah block kiya. Kuch minutes baad try karein.'
        jobs[job_id].update({'status':'error','message':f'❌ {err}','progress':0})

def generate_metadata(video_info, clip_num, total):
    if not model:
        return {
            'title'      : f"Amazing Short #{clip_num} | {video_info['title'][:50]}",
            'description': f"{video_info['title']}\n\n#Shorts #Viral #Trending",
            'tags'       : ['shorts','viral','trending','youtube','youtubeshorts'],
            'hashtags'   : '#Shorts #Viral #Trending #YouTube'
        }
    try:
        prompt = f"""You are a YouTube Shorts expert. Create viral metadata for clip {clip_num} of {total}.

Video: {video_info['title']}
Channel: {video_info['uploader']}

Return ONLY valid JSON (nothing else):
{{
  "title": "Catchy title under 90 chars with emojis",
  "description": "Engaging 100 word description with emojis and call to action",
  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"],
  "hashtags": "#Shorts #Viral #tag1 #tag2 #tag3 #tag4 #tag5"
}}"""
        resp = model.generate_content(prompt)
        text = resp.text.strip()
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except: pass

    return {
        'title'      : f"🔥 Must Watch #{clip_num} | {video_info['title'][:45]}",
        'description': f"Amazing content! {video_info['title']}\n\nLike & Subscribe!\n\n#Shorts #Viral",
        'tags'       : ['shorts','viral','trending','youtube','youtubeshorts'],
        'hashtags'   : '#Shorts #Viral #Trending #YouTube #MustWatch'
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
    youtube = build('youtube','v3',credentials=creds)
    body = {
        'snippet': {
            'title'      : metadata['title'],
            'description': metadata['description'] + '\n\n' + metadata['hashtags'] + '\n\n#Shorts',
            'tags'       : metadata['tags'] + ['shorts','youtubeshorts'],
            'categoryId' : '22'
        },
        'status': {
            'privacyStatus'          : 'public',
            'selfDeclaredMadeForKids': False
        }
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
    req   = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
    resp  = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp.get('id','')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
