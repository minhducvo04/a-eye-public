"""Rewind as model tools for the talker. Import TOOLS into the Anthropic `tools` list and call `run_tool`.

    from server import rewind_tools
    tools = [*other_tools, *rewind_tools.tools_for(session)]   # empty while Rewind is off and has no clips
    # On a tool_use block named search_past or recap_call:
    say_first = rewind_tools.FIRST_LINE[block.name]            # speak this while the tool runs
    result, cards = await rewind_tools.run_tool(block.name, block.input, session, vision=vision, app=app,
                                                turn_id=turn_id)
    for card in cards: await emit(card)                        # rewind_hit / rewind_recap for the client
    tool_result = json.dumps(result)                           # JSON-safe, no image bytes

Results never contain image data; the frame goes to the client card only. Only clips already uploaded while
Rewind was on are searched or recapped.
"""
from server import rewind

SEARCH_PAST_TOOL = {
    'name': 'search_past',
    'description': (
        'Search the recording of THIS call (Rewind) for a past moment or where an object was put. Use it for '
        'past-tense questions about what the user did or where something is, for example "when did I plug in the '
        'CPU fan?", "where did I put the screw?", "did I already connect the 24-pin?", "when did I add the salt?". '
        'Not for what the camera shows right now. Returns sure (our own vision confirmed the object in the frame), '
        'call_time_s (seconds since the call started), label, box ([x,y,w,h] on that frame, 0 to 1), clip_id, '
        'alternatives (other call times) and say (a ready spoken answer). When sure is false, say you are not sure '
        'and offer the times.'),
    'input_schema': {
        'type': 'object',
        'properties': {'query': {'type': 'string', 'description':
                                 'The moment or object to find, short, e.g. "plug in the CPU fan" or "the screw".'}},
        'required': ['query'],
    },
}

RECAP_CALL_TOOL = {
    'name': 'recap_call',
    'description': (
        'Recap THIS call from its Rewind recording: an ordered list of steps the user completed with call times, '
        'and typical steps for the current mode that look skipped. Use when the user says "recap", "what have I '
        'done so far?" or "did I skip anything?". Returns steps ([{t_s, text}]), skipped (typical step names) and '
        'say (a ready spoken summary). Takes 10 to 30 seconds.'),
    'input_schema': {'type': 'object', 'properties': {}},
}

TOOLS = [SEARCH_PAST_TOOL, RECAP_CALL_TOOL]
FIRST_LINE = {'search_past': rewind.CHECKING_LINE, 'recap_call': rewind.RECAP_CHECKING_LINE}


def tools_for(session):
    """Offer the tools only while Rewind is on or this call has clips."""
    current = getattr(session, 'rewind', None)
    return list(TOOLS) if current is not None and (current.on or current.clips) else []


async def run_tool(name, args, session, vision=None, app=None, turn_id=None):
    """(result for tool_result, [client messages]). Unknown names raise KeyError."""
    if name == 'search_past':
        query = str((args or {}).get('query') or '').strip()[:300]
        result = await rewind.search_past(session, query, vision=vision, app=app)
        card = rewind.hit_message(dict(result, query=query), turn_id)
        result.pop('frame_b64', None)
        return result, [card] if card else []
    if name == 'recap_call':
        result = await rewind.recap_call(session, app=app)
        return result, [rewind.recap_message(result, turn_id)]
    raise KeyError(name)


# ---- Local memory across calls (server/memory.py). Data stays on this machine. ----

FIND_ITEM_TOOL = {
    'name': 'find_item',
    'description': (
        "Find where the user last put an object, from their selected memory store across calls and days. "
        "Use for 'where are my keys?', 'where did I leave my glasses?', 'have you seen the blue pill "
        "box?'. Returns found, when (spoken time), place (e.g. 'under the left cup'), home (the tagged home place), "
        "live (the item or its last place is in view now; an arrow is drawn), count, and say (a ready spoken "
        "answer). Prefer this over search_past for objects; use search_past for moments in this call."),
    'input_schema': {'type': 'object', 'properties': {'item': {'type': 'string', 'description':
                     'The object, short, e.g. "keys" or "blue pill box".'}}, 'required': ['item']},
}

TAG_ITEM_TOOL = {
    'name': 'tag_item',
    'description': (
        "Remember an object or place by name for this person, e.g. 'these are my keys, they live in the key "
        "bowl' or 'this is the blue pill box'. Afterwards, sightings of it are kept with a small picture."),
    'input_schema': {'type': 'object', 'properties': {
        'name': {'type': 'string', 'description': 'What the user calls it, e.g. "keys" or "key bowl".'},
        'label': {'type': 'string', 'description': 'What the camera sees, if different, e.g. "keys".'},
        'home': {'type': 'string', 'description': 'Where it belongs, e.g. "blue bowl".'},
        'place_path': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 16,
                       'description': 'Confirmed current location, outermost first, e.g. ["desk", "drawer"]. '
                                      'Omit when only a preferred home is known.'},
        'kind': {'type': 'string', 'enum': ['object', 'place']}}, 'required': ['name']},
}

FORGET_TOOL = {
    'name': 'forget_memory',
    'description': (
        "Really delete memories when the user asks: 'forget that' (the item just discussed), 'forget today', "
        "'forget everything'. Deletes local notes and pictures and any TwelveLabs videos for this person. Returns "
        "count (memories left) and say."),
    'input_schema': {'type': 'object', 'properties': {
        'scope': {'type': 'string', 'enum': ['item', 'today', 'everything']},
        'item': {'type': 'string', 'description': 'For scope item: the object, e.g. "keys".'}},
        'required': ['scope']},
}

MEMORY_TOOLS = [FIND_ITEM_TOOL, TAG_ITEM_TOOL, FORGET_TOOL]
FIRST_LINE.update(find_item='Let me think back.', tag_item='Got it.', forget_memory='Okay.')


def memory_tools_for(session, app):
    """The memory tools while this call is attached to a person's local memory."""
    from server import memory
    store, _ = memory.memories(app).for_call(getattr(session, 'session_id', ''))
    return list(MEMORY_TOOLS) if store is not None else []


async def run_memory_tool(name, args, session, app, turn_id=None):
    """(result for tool_result, [client messages]) for find_item, tag_item, forget_memory."""
    from server import memory
    from server.agent import message, resolve
    store, _ = memory.memories(app).for_call(getattr(session, 'session_id', ''))
    args = args or {}
    if store is None:
        return dict(say="Memory is off for this call."), []
    if name == 'find_item':
        frame = session.frame if getattr(session, 'camera', False) and getattr(session, 'frame', None) else None
        memories = memory.memories(app)
        result = await memory.find(store, str(args.get('item') or '')[:120], embedder=memories.embedder,
                                   scene=getattr(session, 'scene', None), vision=memories.vision,
                                   live_frame=frame and frame['jpeg_b64'])
        cards = [memory_hit_message(result, turn_id, getattr(getattr(session, 'rewind', None), 'started_wall', None))]
        if result.get('live') and result.get('live_scene'):
            turn = dict(agent='helper', draw=[dict(kind='arrow', target=result['live']['object_id'], role='target')])
            cards.append(resolve(turn, result['live_scene'], frame['seq'] if frame else 0)[0])
        result.pop('crop', None)
        result.pop('live_scene', None)
        session.memory_last_item = result.get('item')
        return result, cards
    if name == 'tag_item':
        tag = store.set_tag(args.get('name'), args.get('label'), args.get('home'), args.get('kind', 'object'),
                            place_path=args.get('place_path'))
        line = f"I'll remember your {tag['name']}" + (f", and that they belong in the {tag['home']}." if tag['home'] else '.')
        return dict(tag=tag, say=line), [memory_state_message(store)]
    if name == 'forget_memory':
        scope = args.get('scope')
        item = args.get('item') or getattr(session, 'memory_last_item', None)
        if scope == 'item' and item:
            deleted = store.forget_item(item)

            line = f"Forgotten. I no longer remember your {item}."
        elif scope == 'today':
            from datetime import datetime
            since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            await memory.delete_cloud(store, since=since)
            deleted = store.forget_since(since)
            line = "Forgotten. Nothing from today is kept."
        elif scope == 'everything':
            await memory.delete_cloud(store)
            deleted = store.forget_all()
            line = "Everything is forgotten."
        else:
            return dict(say="Tell me what to forget: that, today, or everything."), []
        state = memory_state_message(store, item if scope == 'item' else None)
        cards = [state]
        rewind = getattr(session, 'rewind', None)
        if rewind is not None:                    # this call's Rewind clips go too; the clip count drops to 0
            from server.rewind import state_message
            await rewind.forget(local=True)
            cards.append(state_message(rewind))
        return dict(deleted=deleted, count=state['count'], count_item=state.get('count_item'), say=line), cards
    raise KeyError(name)


def memory_hit_message(result, turn_id=None, call_started=None):
    """The client card: rewind_hit fields (t_s, frame_b64 = the crop, objects = the item's box on it) plus place."""
    from server.agent import message
    from server.memory import hit_fields
    fields = hit_fields(result, call_started)
    keep = ('item', 'found', 'count', 'count_item', 'when', 'place', 'home', 'say', 'at_ms', 't_s', 'start_s', 'end_s',
            'sure', 'alternatives', 'frame_b64', 'objects')
    out = {k: fields[k] for k in keep if fields.get(k) not in (None, '')}
    if result.get('live'):
        out['live'] = {k: result['live'][k] for k in ('object_id', 'bbox')}
    if turn_id:
        out['turn_id'] = turn_id
    return message('find_item', **out)


def memory_state_message(store, item=None):
    from server import memory
    from server.agent import message
    status = memory.status(store)
    extra = dict(item=item, count_item=store.count(item)) if item else {}
    status['count'] = store.count_today()          # the page shows "Remembering N things today"
    status['retention_days'] = store.settings()['kept_days']
    return message('memory_state', on=True, **{k: status[k] for k in
                   ('memory_id', 'count', 'crops', 'bytes', 'encrypted', 'retention_days')}, **extra)
