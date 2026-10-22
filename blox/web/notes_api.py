"""Research notes endpoints: paste text, upload files (.md/.txt/.csv/.json/.jsonl or a .zip of them), list, delete;
and the title closeness check against the imported reference titles."""
from flask import abort, jsonify, request

from .. import db as dbmod
from ..research import notes
from ..story import originality

TITLE_CHECK_MAX = 300


def register(app):
    @app.get('/api/research/notes')
    def research_notes():
        d = dbmod.get()
        return jsonify(notes=notes.list_notes(d), hints=notes.hints(d), kinds=sorted(notes.KINDS),
                       caps={'text_chars': notes.TEXT_CAP, 'upload_mb': notes.UPLOAD_CAP // (1024 * 1024),
                             'zip_members': notes.ZIP_MEMBERS_MAX},
                       disclaimer=notes.HINTS_NOTE)

    @app.post('/api/research/notes')
    def research_notes_add():
        d = dbmod.get()
        f = request.files.get('file')
        if f is not None and f.filename:
            data = f.stream.read(notes.UPLOAD_CAP + 1)
            ids = notes.add_file(f.filename, data, d)
            return jsonify(ids=ids, count=len(ids))
        body = request.get_json(silent=True) or {}
        text = body.get('text') if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Paste some text or choose a file')
        if len(text) > notes.TEXT_CAP * 2:
            raise ValueError(f'Pasted notes are limited to {notes.TEXT_CAP} characters')
        kind = str(body.get('kind') or 'text')
        if kind not in set(notes.KINDS.values()):
            raise ValueError('Unsupported note kind')
        nid = notes.add(text, title=str(body.get('title') or '')[:160], source=str(body.get('source') or 'paste')[:200],
                        kind=kind, d=d)
        return jsonify(ids=[nid], count=1, note=notes.get(nid, d))

    @app.get('/api/research/notes/<nid>')
    def research_note(nid):
        n = notes.get(nid, dbmod.get())
        if not n:
            abort(404)
        return jsonify(n)

    @app.delete('/api/research/notes/<nid>')
    def research_note_delete(nid):
        if not notes.delete(nid, dbmod.get()):
            abort(404)
        return jsonify(ok=True)

    @app.get('/api/title-check')
    def title_check():
        """How close a title is to the imported reference titles (the editor's title hint)."""
        title = request.args.get('title', '')
        if not title.strip():
            raise ValueError('Enter a title to check')
        if len(title) > TITLE_CHECK_MAX:
            raise ValueError(f'Titles are limited to {TITLE_CHECK_MAX} characters')
        refs = notes.reference_titles(dbmod.get())
        return jsonify(dict(originality.title_check(title, refs=refs), references=len(refs)))
