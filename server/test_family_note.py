import pytest
from fastapi.testclient import TestClient

from server.family_note import Store, build_note, validate_note


def test_note_rules():
    log = {'task': 'sink', 'detail': 0}
    note = build_note(log)
    assert len(note.split('. ')) <= 3
    assert note.endswith('?')
    validate_note({'text': note}, log)
    for payload in ({'text': note, 'pictures': []}, {'text': note + ' Extra.'},
                    {'text': note.replace('?', '.')}, {'text': 'Remember last week?'}):
        with pytest.raises(ValueError):
            validate_note(payload, log)


@pytest.mark.parametrize('confirmation', ['', 'yes', 'no', 'send it', 'maybe', None])
def test_refused_without_send(confirmation):
    s = Store()
    c = s.add_contact('owner', {'name': 'Fixture Friend', 'relation': 'friend', 'consent': True})
    draft = s.draft('owner', {'task': 'sink', 'detail': 0}, c['id'])
    with pytest.raises(ValueError):
        s.send('owner', draft['id'], confirmation)
    assert not s.links
    with pytest.raises(ValueError):
        s.send('owner', draft['id'], 'send')  # cancelled, never retry an old draft


def test_single_use_expiry_and_ownership():
    now = [100]
    s = Store(clock=lambda: now[0])
    assert s.contacts == {}
    with pytest.raises(ValueError):
        s.add_contact('owner', {'name': 'Fixture', 'relation': 'friend', 'consent': False})
    c = s.add_contact('owner', {'name': 'Fixture', 'relation': 'friend', 'consent': True})
    d = s.draft('owner', {'task': 'sink', 'detail': 0}, c['id'])
    with pytest.raises(ValueError):
        s.send('other', d['id'], 'send')
    with pytest.raises(ValueError):
        s.send('owner', d['id'], 'send')  # must finish read-aloud first
    d = s.draft('owner', {'task': 'sink', 'detail': 0}, c['id'])
    s.heard('owner', d['id'])
    link = s.send('owner', d['id'], 'send')
    token = link['delivery_link'].split('#')[1]
    opened = s.exchange(token)
    with pytest.raises(ValueError):
        s.exchange(token)
    assert opened['text'] == d['text']
    s.reply(opened['reply_token'], 'Nice work!')
    assert s.replies('owner') == [{'id': d['id'], 'name': 'Fixture', 'text': 'Nice work!'}]
    assert s.replies('other') == []
    with pytest.raises(ValueError):
        s.reply(opened['reply_token'], 'Again')
    d = s.draft('owner', {'task': 'sink', 'detail': 1}, c['id'])
    s.heard('owner', d['id'])
    token = s.send('owner', d['id'], 'send')['delivery_link'].split('#')[1]
    now[0] += 86400
    with pytest.raises(ValueError):
        s.exchange(token)


def test_forty_note_cases():
    # 20 handwritten acceptable combinations; twenty deliberately altered notes.
    cases = [('sink', i) for i in range(8)] + [('battery', i) for i in range(6)] + [('chair', i) for i in range(6)]
    for task, detail in cases:
        log = {'task': task, 'detail': detail}
        note = build_note(log)
        validate_note({'text': note}, log)
    invalid = [
        'I fixed the sink leak. I tightened the fitting. It is done.',
        'I fixed the sink leak. I tightened the fitting. Can you help? Is that okay?',
        'I fixed the sink leak. I tightened the fitting. It was easy. What next?',
        'I fixed the sink leak. I tightened the fitting. Right?',
        'I fixed the sink leak. I attached a photo. What next?',
        'I fixed the sink leak. Here is the audio recording. What next?',
        'I fixed the sink leak. Here is a video. What next?',
        'I fixed the sink leak at 123 Fixture Street. What next?',
        'I fixed the sink leak. Last week you told me how. What next?',
        'I fixed the sink leak. I remembered our previous call. What next?',
        'I fixed the sink leak. My location is 12.34, 56.78. What next?',
        'I fixed the sink leak. https://example.invalid/photo.png What next?',
        'I fixed the sink leak. data:image/png;base64,AAAA What next?',
        'I fixed the sink leak. I bought a new tap. What next?',
        'I fixed the sink leak. I feel closer to you now. What next?',
        'I replaced the smoke alarm battery. I tightened the fitting. What next?',
        'How do you keep chair screws from working loose?',
        '',
        'I fixed the sink leak! I tightened the fitting! Call me!',
        'I fixed the sink leak. I tightened the fitting. How do you keep it tight? Extra text',
    ]
    for text in invalid:
        with pytest.raises(ValueError):
            validate_note({'text': text}, {'task': 'sink', 'detail': 0})
    print('Family note corpus: 20 accepted / 20 rejected - PASS')


def test_spoken_confirmation_and_revoke():
    s = Store()
    c = s.add_contact('o', {'name': 'Fixture', 'relation': 'friend', 'consent': True})
    d = s.draft('o', {'task': 'sink', 'detail': 0}, c['id'])
    s.heard('o', d['id'])
    assert not s.consume_spoken('other', 'send')
    assert s.consume_spoken('o', ' SEND ')
    assert len(s.links) == 1
    token = s.contacts['o'][c['id']]['delivery_link'].split('#')[1]
    opened = s.exchange(token)
    s.remove_contact('o', c['id'])
    with pytest.raises(ValueError):
        s.reply(opened['reply_token'], 'Fixture reply')
    assert s.replies('o') == []


def test_authenticated_call_roundtrip(monkeypatch):
    from server.app import create_app
    from server.rewind import registry
    from server.auth import mint_owner, token_owner
    from types import SimpleNamespace
    monkeypatch.setenv('A_EYE_TOKEN', 'fixture-token')
    app = create_app()
    capability = mint_owner()
    headers = {'Authorization': 'Bearer fixture-token', 'X-Aeye-Owner': capability}
    owner = token_owner(SimpleNamespace(headers={'x-aeye-owner': capability}, query_params={}))
    registry(app).open('fixture-call', owner=owner)
    client = TestClient(app)
    url = '/api/family'
    query = '?session_id=fixture-call'
    assert client.get(url + query, headers=headers).json()['contacts'] == []
    assert client.get(url + query, headers={'Authorization': 'Bearer fixture-token'}).status_code == 404
    contact = client.post(url + '/contact' + query, headers=headers,
                          json={'name': 'Fixture Friend', 'relation': 'friend', 'consent': True}).json()
    draft = client.post(url + '/draft' + query, headers=headers,
                        json={'contact': contact['id'], 'log': {'task': 'sink', 'detail': 0}}).json()
    assert client.post(url + '/heard' + query, headers=headers, json={'id': draft['id']}).status_code == 200
    sent = client.post(url + '/send' + query, headers=headers,
                       json={'id': draft['id'], 'confirmation': 'send'}).json()
    assert sent['pictures_sent'] == sent['cloud_ai_calls'] == 0
    token = sent['delivery_link'].split('#')[1]
    opened = client.post('/family/exchange', json={'token': token}).json()
    assert client.post('/family/reply', json={'token': opened['reply_token'], 'text': 'Fixture reply', 'audio': ''}).status_code == 400
    assert client.post('/family/reply', json={'token': opened['reply_token'], 'text': 'Fixture reply'}).status_code == 200
    reply = client.get(url + query, headers=headers)
    assert reply.headers['cache-control'] == 'no-store'
    assert reply.json()['replies'][0]['text'] == 'Fixture reply'


def test_routes_auth_limits_and_media(monkeypatch):
    from server.app import create_app
    monkeypatch.setenv('A_EYE_TOKEN', 'fixture-token')
    app = create_app()
    client = TestClient(app)
    assert client.get('/api/family?session_id=x').status_code == 401
    assert client.post('/family/exchange', json={'token': 'bad'}).status_code == 403
    assert client.post('/family/exchange', json={'token': 'bad', 'image': 'no'}).status_code == 400
    assert client.post('/family/exchange', json={'token': 'bad'}, headers={'Origin': 'https://elsewhere.invalid'}).status_code == 403
    codes = [client.post('/family/exchange', json={'token': 'bad'}).status_code for _ in range(65)]
    assert 429 in codes


@pytest.mark.parametrize('words,expected', [('send', 'sent'), ('Send.', 'sent'), ('Zend.', 'cancelled'), ('no', 'cancelled'), ('', 'cancelled')])
def test_server_stt_confirmation_precedes_brain(monkeypatch, words, expected):
    from server.app import create_app
    from server.family_note import Store
    monkeypatch.setenv('A_EYE_OPEN', '1')
    monkeypatch.delenv('A_EYE_TOKEN', raising=False)
    class Box:
        async def stt(self, pcm):
            return words
        async def aclose(self):
            pass
    app = create_app()
    app.state.box_v2 = Box()
    app.state.family_notes = s = Store()
    c = s.add_contact('local:family-stt', {'name': 'Fixture', 'relation': 'friend', 'consent': True})
    d = s.draft('local:family-stt', {'task': 'sink', 'detail': 0}, c['id'])
    s.heard('local:family-stt', d['id'])
    with TestClient(app) as client, client.websocket_connect('/ws2') as ws:
        ws.send_json(dict(v=2, type='hello', session_id='family-stt', pack='general',
                          camera=False, stt='server', tts='browser'))
        assert ws.receive_json()['state'] == 'listening'
        ws.send_json(dict(v=2, type='audio_in', session_id='family-stt', seq=1,
                          pcm16_b64='AAA=', final=True))
        messages = [ws.receive_json() for _ in range(3)]
        assert [m['stage'] for m in messages] == ['received', 'transcribing', 'done']
        assert s.drafts[d['id']]['status'] == expected


@pytest.mark.parametrize("path", ["/", "/agent.html", "/family-reply.html"])
def test_html_cannot_be_framed(path):
    from server.app import create_app
    response = TestClient(create_app()).get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers.get("content-security-policy") == "frame-ancestors 'none'"
    assert response.headers.get("x-frame-options") == "DENY"
