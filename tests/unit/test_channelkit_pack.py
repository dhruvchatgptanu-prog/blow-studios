"""Channel kit: text pack, image composition with Blender mocked, files on disk, endpoints and media serving."""
import json
import os
import shutil

import pytest
from PIL import Image, ImageDraw

from blox import channel_kit as CK, config, prefs as prefsmod, repo


@pytest.fixture(autouse=True)
def clean_kit_dir():
    """The kit lives under the (session-wide) data directory; each test starts without one."""
    shutil.rmtree(config.DATA_DIR / 'channel_kit', ignore_errors=True)
    yield
    shutil.rmtree(config.DATA_DIR / 'channel_kit', ignore_errors=True)


def _fake_render(work_dir, width, height, body):
    """Stand-in for a Blender RGBA still: a transparent frame with an opaque blob where the subject would be."""
    os.makedirs(work_dir, exist_ok=True)
    im = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle(body, radius=40, fill=(242, 193, 78, 255))
    path = os.path.join(work_dir, 'preview.png')
    im.save(path)
    return path


@pytest.fixture
def mocked_blender(monkeypatch):
    calls = []

    def render_range(m, bibles, solved, f0, f1, work_dir, prefs, quality='final', **kw):
        calls.append({'width': m['width'], 'height': m['height'], 'quality': quality, 'cast': [c['id'] for c in m['cast']],
                      'framing': m['shots'][0]['camera']['framing_start'], 'set': solved.get('set'),
                      'preset': m['setting']['preset'], 'camera_z': solved['camera'][10]['location'][2]})
        w, h = m['width'], m['height']
        if quality == 'preview':
            w, h = w // 2, h // 2
        body = (w * 0.3, h * 0.25, w * 0.7, h * 0.95) if len(m['cast']) == 1 else (w * 0.08, h * 0.2, w * 0.92, h * 0.9)
        return {'preview': _fake_render(work_dir, w, h, body)}
    monkeypatch.setattr(CK.BL, 'render_range', render_range)
    monkeypatch.setattr(CK.BL, 'available', lambda: True)
    return calls


def _no_brand(text):
    low = text.lower()
    return 'roblox' not in low and 'lego' not in low and 'youtube.com' not in low


def test_names_handles_are_original_and_brand_free(db):
    p = prefsmod.get(db)
    chars = repo.characters(d=db)
    names = CK.name_suggestions(p, chars)
    assert len(names) >= 5
    cast_names = {c['name'].lower() for c in chars.values()}
    for n in names:
        assert 'roblox' not in n['name'].lower() and n['name'].lower() not in cast_names
        assert all(g.lower() not in n['name'].lower() for g in p['channel']['game_names'])
        assert CK.HANDLE_RX.match(n['handle']) and 'roblox' not in n['handle']
        assert all(CK.HANDLE_RX.match(h) for h in n['handle_alternatives'])
    assert names[0]['name'] == 'Bloxy & Friends' and names[0]['handle'] == 'bloxyandfriends'
    assert len({n['name'] for n in names}) == len(names)
    # A name that uses another game's name or the forbidden word is filtered out, not published.
    p2 = json.loads(json.dumps(p))
    p2['channel']['game_names'] = ['Friends']
    assert all('Friends' not in n['name'] for n in CK.name_suggestions(p2, chars))
    assert CK._forbidden('Roblox Tales', p, chars) and CK._forbidden('Bloxy', p, chars)


def test_description_upload_defaults_and_steps(db):
    p = prefsmod.get(db)
    chars = repo.characters(d=db)
    desc = CK.description(p, chars, 'Bloxy & Friends')
    assert desc.startswith('Bloxy & Friends: original animated stories about Bloxy, Pip')
    assert 'AI assistance' in desc and 'synthetic' in desc and 'checks every video' in desc
    assert 'New Short every 2 hours' in desc and 'Nothing here is captured gameplay' in desc
    assert len(desc) <= 1000 and _no_brand(desc)
    p['schedule']['interval_minutes'] = 1440
    assert 'New Short every day' in CK.description(p, chars)
    p['schedule']['interval_minutes'] = 60
    p['schedule']['max_per_rolling_24h'] = 6
    assert 'every 1 hour (up to 6 a day)' in CK.description(p, chars)
    ud = CK.upload_defaults(p)
    assert ud['audience'] == 'Not made for kids' and ud['altered_or_synthetic_content'].startswith('Yes')
    assert ud['category'] == 'Gaming' and ud['category_id'] == '20' and ud['language'] == 'English'
    assert ud['tags'] == p['publishing']['tags'] and ud['format'].startswith('Shorts')
    p['publishing']['made_for_kids'] = None
    p['publishing']['synthetic_disclosure'] = False
    ud = CK.upload_defaults(p)
    assert ud['audience'].startswith('Undecided') and ud['altered_or_synthetic_content'].startswith('No disclosure')
    steps = CK.steps(p, chars, 'Bloxy & Friends')
    assert 5 <= len(steps) <= 8
    assert any('/oauth/callback' in s for s in steps) and any('Connections' in s for s in steps)
    assert any('profile.png' in s and '800x800' in s for s in steps)
    assert any('2560x1440' in s and '1546x423' in s for s in steps)
    assert any('not made for kids' in s.lower() for s in steps)


def test_text_pack_only_writes_files_without_blender(db, monkeypatch):
    monkeypatch.setattr(CK.BL, 'available', lambda: False)
    kit = CK.generate(images=False, d=db)
    assert kit['channel_name'] == 'Blox Studio' and kit['images']['renderer'] == 'skipped'
    assert set(kit['files']) == {'kit_json', 'kit_md'}
    md = (config.DATA_DIR / 'channel_kit' / 'kit.md').read_text()
    assert md.startswith('# Channel kit: Blox Studio') and 'Images not rendered yet' in md
    assert '@bloxyandfriends' in md and 'Not made for kids' in md and _no_brand(md.replace(
        'Tags: ' + ', '.join(kit['upload_defaults']['tags']), ''))
    loaded = CK.load()
    assert loaded['generated_at'] == kit['generated_at'] and loaded['files'] == kit['files']
    # Asking for images without Blender records the reason instead of failing the text pack.
    kit = CK.generate(images=True, name='Block Tales', d=db)
    assert kit['channel_name'] == 'Block Tales' and kit['images']['renderer'] == 'skipped'
    assert 'Blender' in kit['images']['error']
    assert kit['brand_rules'] and all(_no_brand(r) for r in kit['brand_rules'])
    actions = [r['action'] for r in db.query('SELECT action FROM audit_log')]
    assert actions.count('channel_kit_generated') == 2


def test_images_with_mocked_blender(db, mocked_blender):
    kit = CK.generate(images=True, d=db)
    assert kit['images']['renderer'] == 'blender' and kit['images']['error'] is None
    calls = mocked_blender
    assert len(calls) == 2
    prof, lineup = calls
    assert prof['width'] == prof['height'] == CK.PROFILE_SIZE and prof['quality'] == 'final'
    assert prof['framing'] == 'medium_close' and prof['cast'] == ['bloxy'] and prof['preset'] == 'studio'
    assert prof['set']['pieces'] == [] and prof['set']['transparent'] is True and prof['set']['lights']
    assert lineup['quality'] == 'preview' and lineup['framing'] == 'wide' and lineup['camera_z'] == 1.15
    assert lineup['cast'][len(lineup['cast']) // 2] == 'bloxy' and len(lineup['cast']) == min(5, len(repo.characters(d=db)))
    assert lineup['width'] / lineup['height'] > 2
    out = config.DATA_DIR / 'channel_kit'
    with Image.open(out / 'profile.png') as im:
        assert im.size == (800, 800) and im.mode == 'RGB'
        assert im.getpixel((4, 4)) == CK._rgb(CK.PROFILE_BG) and im.getpixel((400, 500)) != CK._rgb(CK.PROFILE_BG)
    with Image.open(out / 'profile_round_preview.png') as im:
        assert im.size == (800, 800) and im.getpixel((2, 2)) == (245, 245, 247)
    with Image.open(out / 'banner.png') as im:
        assert im.size == (2560, 1440) and im.mode == 'RGB'
    with Image.open(out / 'banner_mobile_preview.png') as im:
        assert im.size == (1546, 423)
    with Image.open(out / 'lineup.png') as im:
        assert im.mode == 'RGBA' and im.getbbox() == (0, 0, im.width, im.height)
    b = kit['images']['banner']
    sx, sy = b['safe_origin']
    lx, ly, lw, lh = b['lineup_box']
    assert (sx, sy) == ((2560 - 1546) // 2, (1440 - 423) // 2)
    assert lx >= sx and ly >= sy and lx + lw <= sx + 1546 and ly + lh <= sy + 423
    assert b['name_lines'] == ['Blox', 'Studio'] and 48 <= b['name_font_px'] <= 132 and b['tagline_lines']
    assert set(kit['files']) == {'kit_json', 'kit_md', 'profile', 'profile_round_preview', 'banner',
                                 'banner_mobile_preview', 'lineup'}
    assert all((config.DATA_DIR / rel).exists() for rel in kit['files'].values())
    assert set(kit['files'].values()) <= CK.registered_files()
    md = (out / 'kit.md').read_text()
    assert 'profile.png (800x800' in md and 'banner.png (2560x1440' in md
    # A later text-only run keeps the rendered images.
    kit2 = CK.generate(images=False, name='Block Tales', d=db)
    assert kit2['files']['profile'] == 'channel_kit/profile.png' and kit2['images']['note']
    assert CK.load()['channel_name'] == 'Block Tales'


def test_compose_banner_fits_long_names(tmp_path):
    lu = _fake_render(tmp_path, 1800, 600, (100, 100, 1700, 560))
    rep = CK.compose_banner(lu, 'Studded Sky Stories Of The Skyline Obby', 'Original animated stories. New Short every day.',
                            tmp_path / 'b.png', mobile_preview=tmp_path / 'm.png')
    assert len(rep['name_lines']) <= 2 and rep['name_font_px'] >= 48
    sx, sy = rep['safe_origin']
    lx, ly, lw, lh = rep['lineup_box']
    assert lx + lw <= sx + 1546 and ly + lh <= sy + 423 and lw <= int(1546 * 0.6) + 1
    with Image.open(tmp_path / 'b.png') as im:
        assert im.size == (2560, 1440)


def test_channel_kit_endpoints_and_media(client, mocked_blender):
    r = client.get('/api/channel-kit')
    assert r.status_code == 200 and r.get_json()['kit'] is None and r.get_json()['blender'] is True
    assert client.get('/media/channel_kit/kit.md').status_code == 404
    assert client.post('/api/channel-kit/generate', json={'images': False, 'name': 'Roblox Fan Club'}).status_code == 400
    assert client.post('/api/channel-kit/generate', json={'images': False, 'name': 'ab'}).status_code == 400
    r = client.post('/api/channel-kit/generate', json={'images': False, 'name': 'Block Tales'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['kit']['channel_name'] == 'Block Tales' and set(body['downloads']) == {'kit_json', 'kit_md'}
    assert body['downloads']['kit_md'] == '/media/channel_kit/kit.md'
    r = client.get('/media/channel_kit/kit.md')
    assert r.status_code == 200 and b'# Channel kit: Block Tales' in r.data
    assert client.get('/media/channel_kit/profile.png').status_code == 404  # not rendered yet
    assert client.get('/media/channel_kit/../studio.db').status_code == 404
    assert client.get('/media/channel_kit/other.png').status_code == 404
    r = client.post('/api/channel-kit/generate', json={'images': True})
    assert r.status_code == 200
    body = r.get_json()
    assert body['kit']['images']['renderer'] == 'blender' and 'profile' in body['downloads']
    r = client.get('/media/channel_kit/profile.png')
    assert r.status_code == 200 and r.data.startswith(b'\x89PNG')
    assert client.get('/media/channel_kit/banner.png').status_code == 200
    r = client.get('/api/channel-kit')
    assert r.get_json()['kit']['channel_name'] == 'Block Tales'


def test_generate_without_blender_refused_by_endpoint(client, monkeypatch):
    monkeypatch.setattr(CK.BL, 'available', lambda: False)
    r = client.post('/api/channel-kit/generate', json={'images': True})
    assert r.status_code == 400 and 'Blender' in r.get_json()['error']
    assert client.get('/api/channel-kit').get_json()['blender'] is False
