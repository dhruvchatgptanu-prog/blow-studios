import json, subprocess, textwrap
from pathlib import Path
from core import FILES, asset

def run(args):
    r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
    if r.returncode: raise ValueError('Media processing failed: '+r.stderr.decode(errors='replace')[-500:])
    return r.stdout
def duration(path):
    return float(run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)]))
def stamp(t):
    n=round(t*1000); return f'{n//3600000:02}:{n//60000%60:02}:{n//1000%60:02},{n%1000:03}'
def captions(p,path):
    blocks=[]; t=0
    for s in p['scenes']:
        words=s['narration'].split(); groups=[words[i:i+6] for i in range(0,len(words),6)]
        for i,g in enumerate(groups):
            a=t+s['duration']*i/len(groups); b=t+s['duration']*(i+1)/len(groups)
            text=' '.join(g).replace('<','').replace('>','').replace('{','').replace('}','')
            blocks.append(f'{len(blocks)+1}\n{stamp(a)} --> {stamp(b)}\n{text}\n')
        t+=s['duration']
    path.write_text('\n'.join(blocks),encoding='utf-8')

def render(p,state,jid):
    work=FILES / ('work_'+jid); work.mkdir(exist_ok=True)
    w,h=(720,1280) if p['format']=='shorts' else (1280,720)
    parts=[]
    for i,s in enumerate(p['scenes']):
        src=asset(s['clip'],'video') if s.get('clip') else FILES / state['clips'][str(i)]['file']
        if duration(src)+0.05<s['duration']+s.get('trim',0): raise ValueError(f'Scene {i+1}: clip is shorter than trim plus duration')
        out=work / f'part{i}.mp4'; args=['ffmpeg','-y','-v','error','-ss',str(s.get('trim',0)),'-i',str(src)]
        audio=FILES / f'{jid}_voice{i}.mp3'
        if p['narration'] and s['narration'].strip():
            if not audio.exists(): raise ValueError('Narration missing')
            ratio=duration(audio)/s['duration']
            if ratio>1.35: raise ValueError(f'Scene {i+1}: shorten narration for clear speech')
            args+=['-i',str(audio)]; af=f'atempo={max(1,ratio):.4f},apad,atrim=duration={s["duration"]},volume={p["voice_volume"]}'
        else:
            args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']; af='anull'
        args+=['-map','0:v:0','-map','1:a:0','-t',str(s['duration']),'-vf',
          f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30',
          '-af',af,'-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(out)]
        run(args); parts.append(out)
    listing=work/'concat.txt'; listing.write_text('\n'.join(f"file '{x.name}'" for x in parts))
    joined=work/'joined.mp4'
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','1','-i',str(listing),'-c','copy',str(joined)])
    out=FILES / f'{jid}_final.mp4'; srt=work/'captions.srt'; captions(p,srt)
    args=['ffmpeg','-y','-v','error','-i',str(joined)]
    if p.get('music'):
        args+=['-stream_loop','-1','-i',str(asset(p['music'],'audio')),'-filter_complex',
          f'[1:a]volume={p["music_volume"]}[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0[a]', '-map','0:v','-map','[a]']
    if p['captions'] and srt.stat().st_size:
        # work paths are generated hex IDs, never untrusted user text.
        args+=['-vf',f"subtitles='{srt}':force_style='FontName=DejaVu Sans,FontSize=20,Outline=2,MarginV=35'"]
    args+=['-c:v','libx264','-preset','fast','-crf','20','-c:a','aac','-movflags','+faststart','-t',str(sum(s['duration'] for s in p['scenes'])),str(out)]
    run(args)
    if duration(out)<1: raise ValueError('Empty rendered video')
    return out.name
