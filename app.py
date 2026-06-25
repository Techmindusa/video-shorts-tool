import os
import re
import json
import time
import subprocess
import threading
from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import yt_dlp

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'videotool-secret-2024')
CORS(app)

# ── CONFIG ────────────────────────────────────────────────────
GEMINI_API_KEY     = os.environ.get('GEMINI_API_KEY', '')
YT_CLIENT_ID       = os.environ.get('YT_CLIENT_ID', '')
YT_CLIENT_SECRET   = os.environ.get('YT_CLIENT_SECRET', '')
REDIRECT_URI       = os.environ.get('REDIRECT_URI', 'http://localhost:5000/oauth2callback')

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Job status track karne ke liye
jobs = {}

# ── HELPERS ───────────────────────────────────────────────────
def get_video_info(url):
    """Video ki basic info fetch karo"""
    ydl_opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            'title': info.get('title', ''),
            'duration': info.get('duration', 0),
            'description': info.get('description', ''),
            'uploader': info.get('uploader', ''),
            'thumbnail': info.get('thumbnail', '')
        }

def download_video(url, output_path, job_id):
    """Video download karo"""
    jobs[job_id]['status'] = 'downloading'
    jobs[job_id]['progress'] = 10
    jobs[job_id]['message'] = 'Video download ho rahi hai...'

    def progress_hook(d):
        if d['status'] == 'downloading':
            try:
                pct = d.get('_percent_str', '0%').strip().replace('%', '')
                jobs[job_id]['progress'] = 10 + int(float(pct) * 0.3)
            except:
                pass

    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'outtmpl': output_path,
        'progress_hooks': [progress_hook],
        'merge_output_format': 'mp4',
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def get_video_duration(filepath):
    """Video ki duration seconds mein"""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())

def find_best_moments(duration, num_clips=3):
    """Best moments ke timestamps nikalo"""
    clips = []
    # Shuruat, middle, aur near-end se clips
    positions = [0.1, 0.45, 0.75]
    clip_duration = min(58, duration / num_clips)  # Max 58 sec (YouTube Shorts limit)

    for pos in positions[:num_clips]:
        start = duration * pos
        clips.append({
            'start': int(start),
            'duration': int(clip_duration)
        })
    return clips

def create_short_video(input_path, output_path, start_sec, duration_sec, job_id, clip_num):
    """FFmpeg se short video banao — vertical format (9:16)"""
    jobs[job_id]['message'] = f'Short clip {clip_num} ban rahi hai...'

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start_sec),
        '-i', input_path,
        '-t', str(duration_sec),
        # Vertical 9:16 format for YouTube Shorts
        '-vf', 'crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
        '-c:v', 'libx264',
        '-c:a', 'aac',
        '-b:v', '4M',
        '-b:a', '128k',
        '-r', '30',
        '-movflags', '+faststart',
        output_path
    ]
    subprocess.run(cmd, capture_output=True)

def generate_ai_metadata(video_info, clip_num, total_clips):
    """Gemini se title, description, hashtags generate karo"""
    prompt = f"""
Tum ek YouTube Shorts expert ho. Niche ek video ki information hai.
Is video ke ek short clip ke liye catchy metadata generate karo.

Original Video Title: {video_info['title']}
Original Description: {video_info['description'][:500] if video_info['description'] else 'N/A'}
Channel: {video_info['uploader']}
Clip Number: {clip_num} of {total_clips}

Generate karo (JSON format mein):
{{
  "title": "Catchy YouTube Shorts title (max 100 chars, Hindi ya English)",
  "description": "Engaging description (200-300 words, emojis use karo)",
  "tags": ["tag1", "tag2", "tag3", ... (15-20 relevant tags)],
  "hashtags": "#tag1 #tag2 #tag3 (10 hashtags)"
}}

Rules:
- Title mein trending words use karo
- Description mein call-to-action daalo
- Tags SEO-friendly honein
- Sirf JSON return karo, kuch aur mat likho
"""
    response = model.generate_content(prompt)
    text = response.text.strip()

    # JSON extract karo
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return {
        'title': f"Amazing Clip #{clip_num} | {video_info['title'][:50]}",
        'description': f"Watch this amazing clip!\n\n{video_info['title']}\n\n#shorts #viral",
        'tags': ['shorts', 'viral', 'trending', 'youtube'],
        'hashtags': '#shorts #viral #trending'
    }

def upload_to_youtube(video_path, metadata, credentials_dict, job_id, clip_num):
    """YouTube pe video upload karo"""
    jobs[job_id]['message'] = f'Clip {clip_num} YouTube pe upload ho rahi hai...'

    creds = Credentials(
        token=credentials_dict['token'],
        refresh_token=credentials_dict['refresh_token'],
        token_uri='https://oauth2.googleapis.com/token',
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=SCOPES
    )

    youtube = build('youtube', 'v3', credentials=creds)

    description_full = (
        metadata['description'] + '\n\n' +
        metadata['hashtags'] + '\n\n' +
        '#Shorts'
    )

    body = {
        'snippet': {
            'title': metadata['title'],
            'description': description_full,
            'tags': metadata['tags'] + ['shorts', 'youtubeshorts'],
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
    insert_request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    response = None
    while response is None:
        status, response = insert_request.next_chunk()

    return response.get('id', '')

def process_video_job(url, credentials_dict, num_clips, job_id):
    """Poora process — download → cut → AI metadata → upload"""
    try:
        base_dir = f'/tmp/videotool_{job_id}'
        os.makedirs(base_dir, exist_ok=True)

        # Step 1: Video info fetch karo
        jobs[job_id]['status'] = 'fetching'
        jobs[job_id]['progress'] = 5
        jobs[job_id]['message'] = 'Video information fetch ho rahi hai...'
        video_info = get_video_info(url)
        jobs[job_id]['video_title'] = video_info['title']

        # Step 2: Video download karo
        input_path = f'{base_dir}/original.mp4'
        download_video(url, input_path, job_id)

        # Step 3: Duration check karo
        jobs[job_id]['progress'] = 42
        jobs[job_id]['message'] = 'Video analysis ho rahi hai...'
        duration = get_video_duration(input_path)
        clips_info = find_best_moments(duration, num_clips)

        uploaded_videos = []

        # Step 4: Har clip ke liye
        for i, clip in enumerate(clips_info, 1):
            clip_progress_base = 42 + (i - 1) * (50 // num_clips)

            # Short video banao
            jobs[job_id]['progress'] = clip_progress_base + 2
            output_path = f'{base_dir}/short_{i}.mp4'
            create_short_video(input_path, output_path, clip['start'], clip['duration'], job_id, i)

            # AI metadata generate karo
            jobs[job_id]['progress'] = clip_progress_base + 8
            jobs[job_id]['message'] = f'Clip {i} ke liye AI title/description ban raha hai...'
            metadata = generate_ai_metadata(video_info, i, num_clips)

            # YouTube pe upload karo
            jobs[job_id]['progress'] = clip_progress_base + 12
            video_id = upload_to_youtube(output_path, metadata, credentials_dict, job_id, i)

            uploaded_videos.append({
                'clip_num': i,
                'youtube_id': video_id,
                'youtube_url': f'https://youtube.com/shorts/{video_id}',
                'title': metadata['title'],
                'hashtags': metadata['hashtags']
            })

            # Cleanup clip file
            try:
                os.remove(output_path)
            except:
                pass

        # Cleanup original
        try:
            import shutil
            shutil.rmtree(base_dir)
        except:
            pass

        # Done!
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        jobs[job_id]['message'] = f'✅ {num_clips} shorts successfully upload ho gayi!'
        jobs[job_id]['uploaded_videos'] = uploaded_videos

    except Exception as e:
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['message'] = f'Error: {str(e)}'
        jobs[job_id]['progress'] = 0

# ── ROUTES ────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({'status': 'VideoTool API Running', 'version': '1.0'})

@app.route('/auth/login')
def auth_login():
    """YouTube OAuth login"""
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id': YT_CLIENT_ID,
                'client_secret': YT_CLIENT_SECRET,
                'redirect_uris': [REDIRECT_URI],
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token'
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    return jsonify({'auth_url': auth_url})

@app.route('/oauth2callback')
def oauth2callback():
    """OAuth callback"""
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id': YT_CLIENT_ID,
                'client_secret': YT_CLIENT_SECRET,
                'redirect_uris': [REDIRECT_URI],
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token'
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=session.get('state')
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    creds_dict = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes) if creds.scopes else SCOPES
    }
    # Frontend ko credentials bhejo
    creds_json = json.dumps(creds_dict)
    return f"""
    <html><body>
    <script>
      window.opener && window.opener.postMessage({{credentials: {creds_json}}}, '*');
      document.write('<h2>✅ Login Successful! Yeh window band kar sakte ho.</h2>');
      setTimeout(() => window.close(), 2000);
    </script>
    </body></html>
    """

@app.route('/process', methods=['POST'])
def process_video():
    """Video processing shuru karo"""
    data = request.json
    url = data.get('url', '').strip()
    credentials = data.get('credentials', {})
    num_clips = int(data.get('num_clips', 3))

    if not url:
        return jsonify({'success': False, 'error': 'Video URL required'})
    if not credentials:
        return jsonify({'success': False, 'error': 'YouTube login required'})
    if num_clips < 1 or num_clips > 5:
        num_clips = 3

    job_id = f'job_{int(time.time() * 1000)}'
    jobs[job_id] = {
        'status': 'starting',
        'progress': 0,
        'message': 'Process shuru ho raha hai...',
        'video_title': '',
        'uploaded_videos': []
    }

    # Background thread mein chalaao
    thread = threading.Thread(
        target=process_video_job,
        args=(url, credentials, num_clips, job_id)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})

@app.route('/status/<job_id>')
def get_status(job_id):
    """Job status check karo"""
    if job_id not in jobs:
        return jsonify({'success': False, 'error': 'Job not found'})
    return jsonify({'success': True, **jobs[job_id]})

@app.route('/video-info', methods=['POST'])
def video_info():
    """Video info preview"""
    url = request.json.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'URL required'})
    try:
        info = get_video_info(url)
        return jsonify({'success': True, **info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
