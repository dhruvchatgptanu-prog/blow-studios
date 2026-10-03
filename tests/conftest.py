import os, tempfile, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['BLOX_DATA']=tempfile.mkdtemp(prefix='blox-tests-')
os.environ['ADMIN_PASSWORD']='test-only-password'
import pytest
import core

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in ['OPENAI_API_KEY','RUNWAYML_API_SECRET','GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','YOUTUBE_REFRESH_TOKEN']:
        monkeypatch.delenv(key,raising=False)
    with core.db() as c:
        for table in ['projects','jobs','assets','reservations','settings']: c.execute('DELETE FROM '+table)

@pytest.fixture
def client():
    from app import app
    app.config['TESTING']=True
    client=app.test_client()
    with client.session_transaction() as s: s['owner']=True; s['csrf']='test-csrf'
    return client

@pytest.fixture
def project():
    p=core.new_project('The last jump')
    p['scenes']=[{'setting':'Floating platforms','expression':'Surprised eyes, determined smile',
       'action':'Jump, land and celebrate','camera':'Medium follow shot','narration':'One leap. One chance.',
       'duration':5,'trim':0,'clip':''}]
    core.save_project(p); return p
