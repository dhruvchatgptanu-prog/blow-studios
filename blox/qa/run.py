"""Run every QA layer on a rendered video and produce the report + verdict."""
import json
import os

from . import motion, story, technical, vision
from .report import Checks, summary, verdict


def run_all(m, solved, telemetry_paths, line_audio, assembly, prefs, video_id='local', render_id='r',
            allow_paid=True):
    ck = Checks(m['fps'])
    final = assembly['final']
    tech = technical.run(ck, m, final, prefs, assembly, dialog_wav=assembly['mix']['paths']['dialog'])
    tele = motion.load_telemetry(telemetry_paths)
    envs = motion.dialog_envelopes(m, line_audio)
    motion.run(ck, m, solved, tele, prefs, assembly['joined'], envs)
    asr = None
    if allow_paid:
        try:
            asr = story.transcribe_final(final, prefs, video_id, render_id)
        except Exception as e:  # ASR is optional evidence; record why it did not run
            ck.add('asr', 'Speech recognition of the final mix', 'story', 'skipped', 'info',
                   {'reason': str(e)[:300]}, 0.5, 'asr')
    story.run(ck, m, line_audio, assembly, prefs, video_id, render_id, asr)
    has_gen = any(s['renderer'] == 'runway' for s in m['shots'])
    if allow_paid:
        try:
            vision.run(ck, m, assembly['joined'], os.path.join(os.path.dirname(final), 'strips'), prefs, video_id,
                       render_id, has_gen)
        except Exception as e:
            ck.add('vision_review', 'Multimodal review of motion windows', 'visual', 'uncertain' if has_gen else 'skipped',
                   'major' if has_gen else 'info', {'reason': 'review failed: ' + str(e)[:300]}, 0.3, 'vision_model')
    else:
        vision.run(ck, m, assembly['joined'], '', dict(prefs, qa=dict(prefs['qa'], vision_review='off')), video_id,
                   render_id, has_gen)
    v, reasons = verdict(ck.items, prefs)
    report = {'verdict': v, 'reasons': [r['id'] for r in reasons], 'summary': summary(ck.items), 'checks': ck.items,
              'technical': {k: tech.get(k) for k in ('frames', 'loudness')},
              'disclaimer': 'Automatic QA is probabilistic. Passing checks reduce, but do not eliminate, the chance '
                            'of a defect reaching viewers.'}
    with open(os.path.join(os.path.dirname(final), 'qa_report.json'), 'w') as f:
        json.dump(report, f, indent=1, default=str)
    return report
