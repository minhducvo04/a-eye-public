"""Make-loaded synthetic Spacetime write/read/delete. No personal rows or owner claim."""
import json
import os
from uuid import uuid4

from cryptography.fernet import Fernet

from server.memory_storage import SpacetimeStore, StorageError


def run(client=None):
    names = ('SPACETIMEDB_URI', 'SPACETIMEDB_MODULE', 'SPACETIMEDB_TOKEN')
    present = [bool(os.environ.get(name)) for name in names]
    print('SpacetimeDB env: ' + ', '.join(f'{name}={"set" if exists else "unset"}'
                                       for name, exists in zip(names, present)))
    if not all(present):
        print('SpacetimeDB live: SKIP (configuration incomplete; no network request)')
        return 0
    store = SpacetimeStore('synthetic-probe', client=client)
    # Ephemeral probe-only key; never reads a real memory key or binds the team's token.
    cipher = Fernet(Fernet.generate_key())
    tid, pid = uuid4().hex, uuid4().hex

    def seal(value):
        return cipher.encrypt(json.dumps(value).encode()).decode()

    def rows():
        response = store._request('/sql', query=f"SELECT id, name FROM my_things WHERE id = '{tid}';")
        return response.json()[0]['rows']

    success = False
    try:
        store._call('place_thing', tid, seal('synthetic probe'), seal('object'), seal(0),
                    pid, seal(['synthetic desk', 'synthetic bin']), seal(0), seal('tag'), seal(1))
        found = rows()
        if len(found) != 1 or found[0][0] != tid or json.loads(cipher.decrypt(found[0][1].encode())) != 'synthetic probe':
            raise StorageError('synthetic read mismatch')
        response = store._request('/sql', query=f"SELECT id, place_path FROM my_placements WHERE id = '{pid}';")
        placements = response.json()[0]['rows']
        if (len(placements) != 1 or placements[0][0] != pid or
                json.loads(cipher.decrypt(placements[0][1].encode())) != ['synthetic desk', 'synthetic bin']):
            raise StorageError('synthetic placement read mismatch')
        print('SpacetimeDB live: PASS (synthetic thing + placement written and read)')
        success = True
    finally:
        # Also attempted after a timeout: a failed response can still have committed.
        store._call('delete_memory_rows', [tid], [pid], [], [])
        if rows():
            raise StorageError('synthetic cleanup verification failed')
        response = store._request('/sql', query=f"SELECT id FROM my_placements WHERE id = '{pid}';")
        if response.json()[0]['rows']:
            raise StorageError('synthetic placement cleanup verification failed')
        print('SpacetimeDB cleanup: PASS (synthetic rows absent)')
    return 0 if success else 1


if __name__ == '__main__':
    try:
        code = run()
    except Exception:
        # Neither SDK errors nor server response bodies may reveal credentials or personal rows.
        print('SpacetimeDB live: FAIL (request, schema, privacy gate or cleanup failed; details withheld)')
        code = 1
    raise SystemExit(code)
