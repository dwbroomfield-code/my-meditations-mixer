from flask import Flask, request, jsonify, Response
import subprocess
import requests
import os
import uuid
import tempfile
import logging

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_file(url, suffix):
    try:
        logger.info(f"Downloading file from: {url}")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(r.content)
        tmp.close()
        logger.info(f"Downloaded {len(r.content)} bytes to {tmp.name}")
        return tmp.name
    except Exception as e:
        logger.error(f"Failed to download {url}: {str(e)}")
        raise

@app.route('/mix', methods=['POST'])
def mix():
    voice_file = None
    music_file = None
    soundscape_file = None
    output_file = None
    
    try:
        data = request.json
        logger.info(f"Mix request received: {data}")
        
        voice_url = data['voice_url']
        music_url = data.get('music_url')
        soundscape_url = data.get('soundscape_url')
        voice_vol = float(data.get('voice_volume', 70)) / 100
        music_vol = float(data.get('music_volume', 40)) / 100
        soundscape_vol = float(data.get('soundscape_volume', 30)) / 100
        extension_seconds = int(data.get('extension_minutes', 0)) * 60
        duration = int(data.get('duration', 0))

        voice_file = download_file(voice_url, '.webm')
        output_file = f'/tmp/{uuid.uuid4()}.mp3'

        inputs = ['-i', voice_file]
        filter_parts = [f'[0:a]volume={voice_vol}[v]']
        mix_inputs = '[v]'
        num_inputs = 1

        if music_url:
            music_file = download_file(music_url, '.mp3')
            inputs += ['-i', music_file]
            filter_parts.append(f'[{num_inputs}:a]volume={music_vol}[m]')
            mix_inputs += '[m]'
            num_inputs += 1

        if soundscape_url:
            soundscape_file = download_file(soundscape_url, '.mp3')
            inputs += ['-i', soundscape_file]
            filter_parts.append(f'[{num_inputs}:a]volume={soundscape_vol}[s]')
            mix_inputs += '[s]'
            num_inputs += 1

        total_duration = duration + extension_seconds
        filter_parts.append(f'{mix_inputs}amix=inputs={num_inputs}:duration=longest[out]')
        filter_complex = ';'.join(filter_parts)

        logger.info(f"Filter complex: {filter_complex}")
        logger.info(f"Total duration: {total_duration}")

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex', filter_complex,
            '-map', '[out]',
            '-t', str(total_duration) if total_duration > 0 else '9999',
            '-b:a', '128k',
            output_file
        ]

        logger.info(f"Running ffmpeg command: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info(f"ffmpeg completed successfully")

        with open(output_file, 'rb') as f:
            mp3_data = f.read()

        logger.info(f"Generated MP3 file: {len(mp3_data)} bytes")

        return Response(mp3_data, mimetype='audio/mpeg')

    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg error: {e.stderr}")
        return jsonify({'error': f'Audio mixing failed: {e.stderr}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        # Cleanup temp files
        for f in [voice_file, music_file, soundscape_file, output_file]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                    logger.info(f"Cleaned up {f}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {f}: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
