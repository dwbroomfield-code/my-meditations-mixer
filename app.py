from flask import Flask, request, jsonify, Response
import subprocess
import requests
import os
import base64
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
    soundscape_files = []
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
        # Second-precision tail extension when the caller supplies it (e.g. the
        # auto-generator pads the gap between the actual voice length and the
        # requested meditation length); falls back to whole extension_minutes.
        raw_ext = data.get('extension_seconds')
        if raw_ext is None:
            extension_seconds = int(data.get('extension_minutes', 0)) * 60
        else:
            extension_seconds = int(raw_ext)
        duration = int(data.get('duration', 0))

        voice_file = download_file(voice_url, '.webm')
        output_file = f'/tmp/{uuid.uuid4()}.mp3'

        # Lead-in: when backing tracks (music/soundscapes) exist, let them play
        # alone for 5 seconds before the voice enters; voice-only meditations
        # get a shorter 3-second silent breath so the start doesn't feel abrupt
        # (but also doesn't read as a broken file). Skipped when the voice file
        # already carries its own baked-in 5-second pad (AI voices, MP3
        # uploads), so the total lead-in stays 5 seconds, not 10.
        lead_in_ms = 0 if data.get('skip_lead_in') else (5000 if (music_url or soundscape_url) else 3000)
        inputs = ['-i', voice_file]
        filter_parts = [f'[0:a]volume={voice_vol},adelay={lead_in_ms}:all=1[v]']
        mix_inputs = '[v]'
        num_inputs = 1

        # Tail: how long the mix keeps running after the voice ends. With no
        # chosen extension that is 5 seconds for every meditation. When backing
        # tracks (music/soundscapes) exist they keep playing through it and fade
        # out over it; voice-only mixes get the same 5 seconds as silence, so
        # every meditation breathes out instead of cutting off at the last word.
        # The fade is applied to the backing tracks only — the voice never fades.
        has_backing = bool(music_url or soundscape_url or data.get('soundscapes'))
        tail_seconds = extension_seconds if extension_seconds > 0 else 5
        # All backing streams start at 0 and the voice ends at duration +
        # lead-in, so the fade lands at the same point on every stream's own
        # timeline: the last 5 seconds of the tail (or the whole tail when it
        # is shorter than 5 seconds).
        if tail_seconds > 0:
            fade_d = min(tail_seconds, 5)
            fade_st = max(0, duration + lead_in_ms / 1000 + tail_seconds - fade_d)
            backing_fade = f',afade=t=out:st={fade_st}:d={fade_d}'
        else:
            backing_fade = ''

        if music_url:
            music_file = download_file(music_url, '.mp3')
            inputs += ['-i', music_file]
            filter_parts.append(f'[{num_inputs}:a]volume={music_vol}{backing_fade}[m]')
            mix_inputs += '[m]'
            num_inputs += 1

        # Layered soundscapes: up to three tracks, each mixed at
        # soundscape_volume x its baked-in level (0-100), matching the
        # in-app preview. When the array is present it REPLACES the single
        # soundscape_url (which the app still sends as the first track), so
        # the first layer isn't mixed twice.
        soundscapes = data.get('soundscapes') or []
        if soundscapes:
            for idx, sc in enumerate(soundscapes[:3]):
                sc_url = sc.get('url') if isinstance(sc, dict) else None
                if not sc_url:
                    continue
                try:
                    level = float(sc.get('level', 100))
                except (TypeError, ValueError):
                    level = 100
                sc_file = download_file(sc_url, '.mp3')
                soundscape_files.append(sc_file)
                inputs += ['-i', sc_file]
                gain = soundscape_vol * (level / 100.0)
                filter_parts.append(f'[{num_inputs}:a]volume={gain}{backing_fade}[sc{idx}]')
                mix_inputs += f'[sc{idx}]'
                num_inputs += 1
        elif soundscape_url:
            soundscape_file = download_file(soundscape_url, '.mp3')
            inputs += ['-i', soundscape_file]
            filter_parts.append(f'[{num_inputs}:a]volume={soundscape_vol}{backing_fade}[s]')
            mix_inputs += '[s]'
            num_inputs += 1

        total_duration = duration + tail_seconds
        if total_duration > 0:
            total_duration += lead_in_ms / 1000
        # No whole-mix fade: the voice ends naturally (never faded), and the
        # backing tracks carry their own tail fade applied above.
        mix_chain = f'{mix_inputs}amix=inputs={num_inputs}:duration=longest:normalize=0[mixed];[mixed]loudnorm=I=-14:TP=-1:LRA=11[normalized]'
        output_stream = '[normalized]'
        if not has_backing and tail_seconds > 0:
            # Voice-only: the tail is silence. Pad after normalization, so the
            # loudness measurement is still taken on the voice content alone.
            mix_chain += f';[normalized]apad=pad_dur={tail_seconds}[padded]'
            output_stream = '[padded]'
        filter_parts.append(mix_chain)
        filter_complex = ';'.join(filter_parts)

        logger.info(f"Filter complex: {filter_complex}")
        logger.info(f"Total duration: {total_duration}")

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex', filter_complex,
            '-map', output_stream,
            '-t', str(total_duration) if total_duration > 0 else '9999',
            '-b:a', '256k',
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
        for f in [voice_file, music_file, soundscape_file] + soundscape_files + [output_file]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                    logger.info(f"Cleaned up {f}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {f}: {e}")

@app.route('/normalize', methods=['POST'])
def normalize():
    """Loudness-normalize a single audio file to -16 LUFS (the same spoken-word
    target Auphonic applies to clean-voice recordings). Accepts
    {'audio_url': ...} or {'audio_base64': ...}; returns MP3 bytes."""
    input_file = None
    output_file = None
    try:
        data = request.json
        logger.info("Normalize request received")

        if data.get('audio_base64'):
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            tmp.write(base64.b64decode(data['audio_base64']))
            tmp.close()
            input_file = tmp.name
        elif data.get('audio_url'):
            input_file = download_file(data['audio_url'], '.mp3')
        else:
            return jsonify({'error': 'audio_url or audio_base64 required'}), 400

        output_file = f'/tmp/{uuid.uuid4()}.mp3'
        # Optional lead-in: prepend silence (adelay) AFTER normalization, so
        # loudness is measured on the voice content alone. AI voice recordings
        # use this for the same 5-second pad baked into uploaded voice files.
        lead_in_ms = int(data.get('lead_in_ms', 0) or 0)
        filter_str = 'loudnorm=I=-16:TP=-1:LRA=11'
        if lead_in_ms > 0:
            filter_str += f',adelay={lead_in_ms}:all=1'
        cmd = ['ffmpeg', '-y', '-i', input_file,
               '-filter:a', filter_str,
               '-b:a', '128k',
               output_file]
        logger.info(f"Running ffmpeg command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        with open(output_file, 'rb') as f:
            mp3_data = f.read()
        logger.info(f"Normalized MP3 file: {len(mp3_data)} bytes")
        return Response(mp3_data, mimetype='audio/mpeg')

    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg error: {e.stderr}")
        return jsonify({'error': f'Audio normalization failed: {e.stderr}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        for f in [input_file, output_file]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                except Exception as e:
                    logger.warning(f"Failed to cleanup {f}: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
