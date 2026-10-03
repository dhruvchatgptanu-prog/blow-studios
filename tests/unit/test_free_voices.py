"""Free offline voices (Piper): provider wiring, licensing, install verification, previews."""
import copy
import io
import tarfile
import wave

import numpy as np
import pytest

from blox import prefs, videos
from blox.util import Blocked
from blox.voice import piper, tts


def fake_wav(path, seconds=1.2, sr=22050):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * 160 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


@pytest.fixture
def voices(tmp_path, monkeypatch):
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    m = d / piper.DEFAULT_MODEL
    m.mkdir(parents=True)
    (m / f'{piper.DEFAULT_MODEL}.onnx').write_bytes(b'onnx')
    (m / f'{piper.DEFAULT_MODEL}.onnx.json').write_text('{}')
    calls = []
    real_run = piper.media.run

    def run(args, timeout=None, **kw):
        if args[1:3] == ['-m', 'piper']:
            calls.append(args)
            fake_wav(args[args.index('-f') + 1])
            return b'', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(piper.media, 'run', run)
    return calls


def test_piper_line_is_natural_free_and_credited(db, voices, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    assert p['production']['tts_provider'] == 'piper', 'free voices are the default'
    line = {'id': 'l1', 'speaker': 'hero', 'text': 'Wait... where is it?', 'emotion': 'startled', 'pace': 'fast',
            'volume': 'loud'}
    res = tts.synthesize_line(line, {'id': 'ch_bloxy', 'name': 'Bloxy', 'voice': {}}, p, str(tmp_path / 'v'), 'vid1')
    assert res['test_voice'] is False and res['provider'] == 'piper'
    assert res['alignment_kind'] == 'estimated' and res['words']
    assert res['voice_meta']['license'] == 'CC BY 4.0' and 'LibriTTS' in res['voice_meta']['attribution']
    args = voices[-1]
    assert args[args.index('-s') + 1] == '60'
    assert float(args[args.index('--length-scale') + 1]) < 1.0, 'fast pace speaks quicker'
    assert tts.estimate_cost(line['text'], 'piper', p['budget']['prices']) == 0.0


def test_speaker_choice_and_bounds(voices, tmp_path):
    assert piper.speaker_for('ch_pip', {}) == 288
    assert piper.speaker_for('ch_new', {'piper_speaker': 12}) == 12
    assert 0 <= piper.speaker_for('ch_other', {}) < 904
    with pytest.raises(Blocked):
        piper.synthesize('hi', str(tmp_path / 'x.wav'), 904, {'pace': 'normal', 'emotion': 'neutral'})


def test_missing_model_blocks_with_install_hint(db, tmp_path, monkeypatch):
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'none'))
    with pytest.raises(Blocked) as e:
        piper.synthesize('hi', str(tmp_path / 'x.wav'), 1, {'pace': 'normal', 'emotion': 'neutral'})
    assert 'install-voice' in str(e.value)


def test_install_verifies_checksum_and_extracts_only_expected_files(tmp_path, monkeypatch):
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'v'))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data in (('en-us-libritts-high.onnx', b'model'), ('en-us-libritts-high.onnx.json', b'{}'),
                           ('../../evil.sh', b'rm -rf /')):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    monkeypatch.setattr(piper.netsafe, 'fetch', lambda url, provider, dest=None, **kw: open(dest, 'wb').write(blob))
    with pytest.raises(Blocked, match='Checksum'):
        piper.install()
    import hashlib
    spec = dict(piper.VOICES[piper.DEFAULT_MODEL], sha256=hashlib.sha256(blob).hexdigest())
    monkeypatch.setitem(piper.VOICES, piper.DEFAULT_MODEL, spec)
    path = piper.install()
    assert sorted(p.name for p in path.iterdir()) == ['en-us-libritts-high.onnx', 'en-us-libritts-high.onnx.json']
    assert not (tmp_path / 'evil.sh').exists()


def test_description_credits_voice_licence(db):
    from blox.youtube import publisher
    vid = videos.create('t', 'manual', d=db, metadata={'voice_credits': [piper.VOICES[piper.DEFAULT_MODEL]['attribution']],
                                                       'publish_metadata': {'title': 't', 'description': 'A story.'}})
    md = publisher.build_metadata(videos.get(vid, db), 1_800_000_000, prefs.get(db))
    assert 'CC BY 4.0' in md['snippet']['description'] and md['snippet']['description'].endswith('#Shorts')


def test_voice_preview_endpoint_is_free(client, voices):
    r = client.post('/api/characters/ch_pip/voice-preview', json={'piper_speaker': 7})
    j = r.get_json()
    assert r.status_code == 200 and j['speaker'] == 7 and j['preview'].startswith('/media/work/character_previews/')
    assert client.get(j['preview']).status_code == 200
    assert client.post('/api/characters/ch_pip/voice-preview', json={'piper_speaker': 5000}).status_code == 400


def test_readiness_reports_missing_free_voice(client, tmp_path, monkeypatch):
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'empty'))
    r = client.get('/api/state').get_json()['readiness']['voices']
    assert r['ok'] is False and 'install-voice' in r['detail']


def test_character_saves_speaker(client):
    body = {'name': 'Pip', 'active': True, 'bible': {'scale': 0.9, 'palette': {}, 'costume': {}},
            'voice': {'piper_speaker': 42}}
    assert client.put('/api/characters/ch_pip', json=body).status_code == 200
    assert client.get('/api/characters').get_json()['characters']['ch_pip']['voice']['piper_speaker'] == 42
    body['voice']['piper_speaker'] = -1
    assert client.put('/api/characters/ch_pip', json=body).status_code == 400
