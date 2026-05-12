from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi import Body

from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.id3 import APIC

from sentence_transformers import SentenceTransformer
import numpy as np
import json

import sqlite3

import musicbrainzngs

from pathlib import Path
import os
import requests
app = FastAPI()

embedding_model = SentenceTransformer(
        "all-MiniLM-L6-v2"
    )

musicbrainzngs.set_useragent(
    "LocalMusicPlayer",
    "1.0"
)

BASE_DIR = Path(__file__).parent

DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(exist_ok=True)

COVERS_DIR = DATA_DIR / "covers"

COVERS_DIR.mkdir(exist_ok=True)

def read_metadata(path):
    path_str = str(path)

    if path_str.lower().endswith(".mp3"):
        audio = MP3(path_str, ID3=ID3)
        tags = audio.tags or {}

        return {
            "title": tags.get("TIT2").text[0] if tags.get("TIT2") else "Unknown",
            "artist": tags.get("TPE1").text[0] if tags.get("TPE1") else "Unknown",
            "album": tags.get("TALB").text[0] if tags.get("TALB") else "Unknown",
            "length": audio.info.length,
            "type": "mp3"
        }

    elif path_str.lower().endswith(".flac"):
        audio = FLAC(path_str)

        return {
            "title": audio.get("title", ["Unknown"])[0],
            "artist": audio.get("artist", ["Unknown"])[0],
            "album": audio.get("album", ["Unknown"])[0],
            "length": audio.info.length,
            "type": "flac"
        }

    return None

def scan_music():
    music_folder = BASE_DIR / "music"

    songs = []

    for path in music_folder.rglob("*"):

        print("FOUND:", path)

        if path.suffix.lower() not in [".mp3", ".flac"]:
            continue

        path_str = str(path)

        modified = path.stat().st_mtime

        cached = get_cached_song(path_str)

        # USE CACHE IF FILE UNCHANGED
        if cached:

            if cached["modified"] == modified:

                songs.append({
                    "id": len(songs),
                    **cached
                })

                continue

        # RESCAN METADATA
        metadata = read_metadata(path)

        print("METADATA:", metadata)

        if metadata:

            metadata["path"] = path_str

            metadata["modified"] = modified

            save_song(metadata)

            songs.append({
                "id": len(songs),
                **metadata
            })

    return songs

songs_list = []

def get_recommendations(song_id):

    target = load_embedding(song_id)

    if target is None:
        return []

    scored = []

    for song in songs_list:

        if song["id"] == song_id:
            continue

        vector = load_embedding(song["id"])

        if vector is None:
            continue

        similarity = cosine_similarity(
            target,
            vector
        )

        scored.append({
            "song": song,
            "score": float(similarity)
        })

    scored.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return [
        item["song"]
        for item in scored[:10]
    ]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS songs (
        path TEXT PRIMARY KEY,
        title TEXT,
        artist TEXT,
        album TEXT,
        length REAL,
        type TEXT,
        cover TEXT,
        modified REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        song_id INTEGER,
        played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS embeddings (
        song_id INTEGER PRIMARY KEY,
        embedding TEXT
    )
    """)

    conn.commit()
    conn.close()

def create_user(name):

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT INTO users (name)
    VALUES (?)
    """, (name,))

    conn.commit()

    user_id = c.lastrowid

    conn.close()

    return user_id

def get_users():

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    rows = c.execute("""
    SELECT id, name
    FROM users
    ORDER BY name
    """).fetchall()

    conn.close()

    return [
        {
            "id": row[0],
            "name": row[1]
        }
        for row in rows
    ]

def get_cached_song(path):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT * FROM songs WHERE path = ?", (path,))
    row = c.fetchone()

    conn.close()

    if row:
        return {
            "path": row[0],
            "title": row[1],
            "artist": row[2],
            "album": row[3],
            "length": row[4],
            "type": row[5],
            "cover": row[6],
            "modified": row[7]
        }

    return None

def save_song(song):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO songs
    (path, title, artist, album, length, type, cover, modified)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        song["path"],
        song["title"],
        song["artist"],
        song["album"],
        song["length"],
        song["type"],
        song.get("cover"),
        song["modified"]
    ))

    conn.commit()
    conn.close()

def add_history(user_id, song_id):

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT INTO history (user_id, song_id)
    VALUES (?, ?)
    """, (user_id, song_id))

    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    row = c.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    ).fetchone()

    conn.close()

    return row[0] if row else None

def get_cover_path(song_id):

    return COVERS_DIR / f"{song_id}.img"

def load_cached_cover(song_id):

    path = get_cover_path(song_id)

    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()

    return None

def save_cached_cover(song_id, image_bytes):

    path = get_cover_path(song_id)

    path.write_bytes(image_bytes)

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO settings
    (key, value)
    VALUES (?, ?)
    """, (key, value))

    conn.commit()
    conn.close()

def build_embedding_text(song):

    parts = [
        song.get("title", ""),
        song.get("artist", ""),
        song.get("album", "")
    ]

    return " ".join(parts)

def generate_embedding(song):

    text = build_embedding_text(song)

    vector = embedding_model.encode(text)

    return vector.tolist()

def save_embedding(song_id, vector):

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    INSERT OR REPLACE INTO embeddings
    (song_id, embedding)
    VALUES (?, ?)
    """, (
        song_id,
        json.dumps(vector)
    ))

    conn.commit()
    conn.close()

def load_embedding(song_id):

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    row = c.execute("""
    SELECT embedding
    FROM embeddings
    WHERE song_id = ?
    """, (song_id,)).fetchone()

    conn.close()

    if not row:
        return None

    return np.array(
        json.loads(row[0])
    )

def build_all_embeddings():

    for song in songs_list:

        existing = load_embedding(song["id"])

        if existing is not None:
            continue

        vector = generate_embedding(song)

        save_embedding(
            song["id"],
            vector
        )

        print(
            "Embedded:",
            song["title"]
        )

def cosine_similarity(a, b):

    return np.dot(a, b) / (
        np.linalg.norm(a)
        * np.linalg.norm(b)
    )

def find_local_cover(path):

    folder = Path(path).parent

    candidates = [
        "cover.jpg",
        "folder.jpg",
        "front.jpg",
        "cover.png",
        "folder.png",
        "front.png"
    ]

    for name in candidates:

        candidate = folder / name

        if candidate.exists():

            return candidate.read_bytes()

    return None

def fetch_cover_art(song_id, song):

    cached = load_cached_cover(song_id)

    if cached:
        return cached

    try:

        query = (
            f'artist:"{song["artist"]}" '
            f'AND release:"{song["album"]}"'
        )

        result = musicbrainzngs.search_releases(
            query=query,
            limit=1
        )   

        releases = result.get("release-list")

        if not releases:
            return None

        release_id = releases[0]["id"]

        url = (
            "https://coverartarchive.org/release/"
            f"{release_id}/front"
        )

        res = requests.get(
            url,
            timeout=5
        )

        if res.status_code == 200:

            save_cached_cover(
                song_id,
                res.content
            )

            return res.content

    except Exception as e:

        print(
            "MusicBrainz cover error:",
            e
        )

    return None

@app.on_event("startup")
def load_music():
    global songs_list

    init_db() 
    songs_list = scan_music()
    build_all_embeddings()

    print("SONGS FOUND:", len(songs_list))

@app.get("/songs")
def get_songs():
    return songs_list


@app.get("/songs/{song_id}")
def get_song(song_id: int):
    if song_id < 0 or song_id >= len(songs_list):
        raise HTTPException(status_code=404, detail="Song not found")
    return songs_list[song_id]

@app.get("/stream/{song_id}")
def stream(song_id: int, request: Request):
    if song_id < 0 or song_id >= len(songs_list):
        raise HTTPException(status_code=404, detail="Song not found")

    path = songs_list[song_id]["path"]
    file_size = os.path.getsize(path)

    range_header = request.headers.get("range")

    start = 0
    end = file_size - 1

    if range_header:
        bytes_range = range_header.replace("bytes=", "").split("-")
        start = int(bytes_range[0])
        if bytes_range[1]:
            end = int(bytes_range[1])

    chunk_size = end - start + 1

    def file_iterator(file_path, start, length):
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk = 8192

            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                yield data
                remaining -= len(data)

    if path.lower().endswith(".mp3"):
        media_type = "audio/mpeg"
    elif path.lower().endswith(".flac"):
        media_type = "audio/flac"
    else:
        media_type = "application/octet-stream"

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_size),
    }

    return StreamingResponse(
        file_iterator(path, start, chunk_size),
        status_code=206 if range_header else 200,
        media_type=media_type,
        headers=headers,
    )

@app.get("/cover/{song_id}")
def get_cover(song_id: int):

    if song_id < 0 or song_id >= len(songs_list):
        raise HTTPException(
            status_code=404,
            detail="Song not found"
        )

    song = songs_list[song_id]

    path = song["path"]

    # Local Folder Cover
    local_cover = find_local_cover(path)

    if local_cover:

        return Response(
            content=local_cover,
            media_type="image/jpeg"
        )

    # Cache
    cached = load_cached_cover(song_id)

    if cached:

        return Response(
            content=cached,
            media_type="image/jpeg"
        )

    try:

        # Embedded mp3 Art
        if path.lower().endswith(".mp3"):

            audio = ID3(path)

            for tag in audio.values():

                if isinstance(tag, APIC):

                    save_cached_cover(
                        song_id,
                        tag.data
                    )

                    return Response(
                        content=tag.data,
                        media_type="image/jpeg"
                    )

        # Embedded FLAC Art
        elif path.lower().endswith(".flac"):

            audio = FLAC(path)

            if audio.pictures:

                picture = audio.pictures[0]

                save_cached_cover(
                    song_id,
                    picture.data
                )

                return Response(
                    content=picture.data,
                    media_type=picture.mime
                )

    except Exception as e:

        print("Embedded cover error:", e)

    # MusicBrainz Fallback
    fallback = fetch_cover_art(
        song_id,
        song
    )

    if fallback:

        return Response(
            content=fallback,
            media_type="image/jpeg"
        )

    raise HTTPException(
        status_code=404,
        detail="No cover art"
    )

@app.get("/")
def root():
    return RedirectResponse(url="/app")

@app.get("/users")
def users():
    return get_users()


@app.post("/users")
def new_user(data: dict = Body(...)):

    user_id = create_user(data["name"])

    return {
        "id": user_id,
        "name": data["name"]
    }

@app.post("/play/{song_id}")
def play_song(song_id: int, data: dict = Body(...)):

    add_history(data["user_id"], song_id)

    return {"status": "ok"}

@app.get("/recent/{user_id}")
def recent(user_id: int):

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT song_id
        FROM history
        WHERE user_id = ?
        ORDER BY played_at DESC
        LIMIT 10
    """, (user_id,))

    rows = c.fetchall()

    conn.close()

    seen = set()
    recent_songs = []

    for row in rows:

        song_id = row[0]

        if song_id not in seen and song_id < len(songs_list):

            recent_songs.append(
                songs_list[song_id]
            )

            seen.add(song_id)

    return recent_songs

@app.get("/recommend/{song_id}")
def recommend(song_id: int):
    return get_recommendations(song_id)

@app.get("/settings/{key}")
def read_setting(key: str):
    value = get_setting(key)

    return {
        "key": key,
        "value": value
    }

@app.post("/scan")
def scan_library():

    global songs_list

    songs_list = scan_music()

    build_all_embeddings()

    return {
        "status": "ok",
        "songs": len(songs_list)
    }

@app.post("/settings/{key}")
def write_setting(key: str, data: dict = Body(...)):

    set_setting(key, data["value"])

    return {"status": "ok"}

app.mount(
    "/app",
    StaticFiles(directory=BASE_DIR / "static", html=True),
    name="static"
)