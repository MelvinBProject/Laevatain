PROFILES = {-900001: 'Mika', -900002: 'Ren'}


def profile_name(user_id, owner_id):
    if user_id == owner_id:
        return 'Library owner', False
    if user_id in PROFILES:
        return PROFILES[user_id], True
    return f'Discord user {user_id}', False


def seed_profiles(database, guild_id):
    samples = [
        (-900001, 'Late night', [('Spotlight', 'https://www.youtube.com/watch?v=7fkOqXAHLKQ'), ('Evening mix', 'https://www.youtube.com/watch?v=saOnB_CRG9Y')]),
        (-900002, 'Study corner', [('Study session', 'https://www.youtube.com/watch?v=PHdU5sHigYQ')]),
    ]
    with database.connect(True) as db:
        for owner, name, songs in samples:
            found = db.execute('SELECT id FROM playlists WHERE user_id=? AND guild_id=? AND name=?', (owner, guild_id, name)).fetchone()
            if found:
                continue
            playlist = db.execute('INSERT INTO playlists(user_id,guild_id,name) VALUES(?,?,?)', (owner, guild_id, name)).lastrowid
            for position, (title, uri) in enumerate(songs, 1):
                db.execute('INSERT INTO songs(playlist_id,title,uri,author,position) VALUES(?,?,?,?,?)', (playlist, title, uri, 'Demo metadata — not fetched from Discord', position))
