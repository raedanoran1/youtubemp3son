from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from moviepy.editor import AudioFileClip
import os
import shutil
import traceback
from datetime import datetime, timedelta
import logging
from pydub import AudioSegment
import numpy as np
import io
import tempfile

# Yeni import
import yt_dlp

# Logging ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Temizleme işlemi için son indirme zamanını takip etmek için global değişken
last_download_time = None

def cleanup_downloads():
    """24 saatten eski dosyaları temizle"""
    download_path = 'static/downloads'
    if os.path.exists(download_path):
        current_time = datetime.now()
        for filename in os.listdir(download_path):
            file_path = os.path.join(download_path, filename)
            file_modified = datetime.fromtimestamp(os.path.getmtime(file_path))
            if current_time - file_modified > timedelta(hours=24):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Dosya silme hatası: {str(e)}")

def validate_youtube_url(url):
    """YouTube URL'sini doğrula"""
    if not url:
        logger.error("URL boş")
        return False
    
    valid_hosts = ['youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com']
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        logger.info(f"Parsing URL: {url}")
        logger.info(f"Parsed netloc: {parsed.netloc}")
        
        # URL'nin bir şema (http/https) içerdiğinden emin olun
        if not parsed.scheme:
            url = 'https://' + url
            parsed = urlparse(url)
        
        is_valid = any(host in parsed.netloc for host in valid_hosts)
        if not is_valid:
            logger.error(f"Geçersiz host: {parsed.netloc}")
        return is_valid
    except Exception as e:
        logger.error(f"URL doğrulama hatası: {str(e)}")
        return False

def convert_to_432hz(input_path, output_path):
    """MP3 dosyasını 432 Hz'e dönüştür"""
    try:
        # MP3 dosyasını yükle
        audio = AudioSegment.from_mp3(input_path)
        
        # Mevcut frekansı 440 Hz kabul edip, 432 Hz'e dönüştürmek için gerekli oranı hesapla
        ratio = 432.0 / 440.0
        
        # Yeni sample rate hesapla
        new_sample_rate = int(audio.frame_rate * ratio)
        
        # Sample rate'i değiştir
        converted_audio = audio._spawn(audio.raw_data, overrides={
            "frame_rate": new_sample_rate
        })
        
        # Orijinal sample rate'e geri dönüştür
        converted_audio = converted_audio.set_frame_rate(audio.frame_rate)
        
        # Yeni dosyayı kaydet
        converted_audio.export(output_path, format="mp3", bitrate="192k")
        
        return True
    except Exception as e:
        logger.error(f"432 Hz dönüşüm hatası: {str(e)}")
        return False

def download_with_ytdlp(youtube_url):
    """yt-dlp ile video indirme"""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            filename_template = os.path.join(temp_dir, '%(title)s.%(ext)s')
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': filename_template,
                'prefer_ffmpeg': True,
                'keepvideo': False,
                'quiet': False,
                'no_warnings': False
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Video indiriliyor: {youtube_url}")
                info_dict = ydl.extract_info(youtube_url, download=True)
                video_title = info_dict['title']
                
                # Find the downloaded MP3 file in temp directory
                mp3_file = None
                for file in os.listdir(temp_dir):
                    if file.endswith('.mp3'):
                        mp3_file = os.path.join(temp_dir, file)
                        break
                
                if not mp3_file:
                    raise Exception("MP3 dosyası bulunamadı")
                
                # Convert to 432Hz in memory
                audio = AudioSegment.from_mp3(mp3_file)
                ratio = 432.0 / 440.0
                new_sample_rate = int(audio.frame_rate * ratio)
                converted_audio = audio._spawn(audio.raw_data, overrides={
                    "frame_rate": new_sample_rate
                })
                converted_audio = converted_audio.set_frame_rate(audio.frame_rate)
                
                # Save to bytes buffer
                buffer = io.BytesIO()
                converted_audio.export(buffer, format="mp3", bitrate="192k")
                buffer.seek(0)
                
                return buffer, f"{video_title}_432hz.mp3"
                
    except Exception as e:
        logger.error(f"İndirme hatası: {str(e)}")
        traceback.print_exc()
        raise e

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    try:
        data = request.get_json()
        if not data:
            logger.error("JSON verisi alınamadı")
            return jsonify({'error': 'JSON verisi gerekli'}), 400

        youtube_url = data.get('youtube_url')
        logger.info(f"Gelen YouTube URL: {youtube_url}")

        if not youtube_url:
            logger.error("YouTube URL eksik")
            return jsonify({'error': 'YouTube URL gerekli'}), 400

        if not validate_youtube_url(youtube_url):
            logger.error(f"Geçersiz YouTube URL: {youtube_url}")
            return jsonify({'error': 'Geçersiz YouTube URL formatı'}), 400

        try:
            # Download and convert to 432Hz
            buffer, filename = download_with_ytdlp(youtube_url)
            
            # Return the file directly from memory
            return send_file(
                buffer,
                as_attachment=True,
                download_name=filename,
                mimetype='audio/mpeg'
            )

        except Exception as e:
            logger.error(f"Dönüştürme hatası: {str(e)}")
            return jsonify({'error': 'Dönüştürme işlemi başarısız oldu'}), 500

    except Exception as e:
        logger.error(f"Genel hata: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    try:
        # Güvenlik kontrolü: filename'den zararlı karakterleri temizle
        filename = os.path.basename(filename)
        file_path = os.path.join('static', 'downloads', filename)

        if not os.path.exists(file_path):
            logger.error(f"Dosya bulunamadı: {file_path}")
            return jsonify({
                'error': 'Dosya bulunamadı. Lütfen önce dönüştürme işlemini yapın.',
                'file_path': file_path,
                'exists': False
            }), 404

        try:
            return send_file(
                file_path,
                as_attachment=True,
                download_name=filename,
                mimetype='audio/mpeg'
            )
        except Exception as e:
            logger.error(f"Dosya gönderme hatası: {str(e)}")
            return jsonify({'error': 'Dosya gönderme hatası'}), 500

    except Exception as e:
        logger.error(f"Download hatası: {str(e)}")
        return jsonify({'error': f'Dosya indirme hatası: {str(e)}'}), 500

if __name__ == '__main__':
    os.makedirs('static/downloads', exist_ok=True)
    cleanup_downloads()  # Başlangıçta eski dosyaları temizle
    app.run(debug=True)
