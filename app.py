from flask import Flask, request, jsonify
import subprocess
import requests
import os
import uuid
import tempfile

app = Flask(__name__)

def download_file(url, suffix):
    r = requests.get(url, timeout=60)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.close()
    return tmp.name

@app.route('/mix', methods=['POST'])
def mix():
    data = request.json
    voice_url = data['voice_url']
    music_url = data.get('music_url')
    soundscape_url = data.get('soundscape_url')
    voice_vol = float(data.get('voice_volume', 70)) / 100
    music_vol = float(data.get('music_volume', 40)) / 100
    soundscape_vol = float(data.get('soundscape_volume', 30)) / 100
    extension_seconds = int(data.get('extension_minutes', 0)) * 60

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

    total_duration = data.get('duration', 0) + extension_seconds
    filter_parts.append(f'{mix_inputs}amix=inputs={num_inputs}:duration=longest[out]')
    filter_complex = ';'.join(filter_parts)

    cmd = ['ffmpeg', '-y'] + inputs + [
        '-filter_complex', filter_complex,
        '-map', '[out]',
        '-t', str(total_duration) if total_duration > 0 else '9999',
        '-b:a', '128k',
        output_file
    ]

    subprocess.run(cmd, check=True)

    with open(output_file, 'rb') as f:
        mp3_data = f.read()

    os.unlink(voice_file)
    os.unlink(output_file)

    from flask import Response
    return Response(mp3_data, mimetype='audio/mpeg')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
