from flask import Flask, request, redirect, session, jsonify, url_for
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import os
from datetime import date, time, datetime
import time
from urllib.parse import urlencode
import json
import openai
import math
from dotenv import load_dotenv
import threading
import schedule
import pandas as pd
from acrcloud.recognizer import ACRCloudRecognizer
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
CORS(app)



CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')

OPENAI_KEY = os.getenv('OPENAI_KEY')

HOST = os.getenv('HOST')
DATABASE = os.getenv('DATABASE')
DB_USER = os.getenv('DB_USER')
PASSWORD = os.getenv('PASSWORD')



def get_db_connection():
    conn = psycopg2.connect(
        host=HOST,
        database=DATABASE,
        user=DB_USER,
        password=PASSWORD
    )
    return conn

# BAR REDIRECT

@app.route('/barredirect/<int:bar_id>')
def bar_redirect(bar_id):
    custom_url_scheme = f"intoit.ansh://bar/{bar_id}"
    return redirect(custom_url_scheme)


# AI SEARCH 

@app.route('/api/ai_search', methods=['POST'])
def ai_search():
    data = request.json
    query = data.get('query', '')
    email = data.get('email', '')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch user's location from the database
    cursor.execute('SELECT location FROM users WHERE email = %s', (email,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'error': 'User not found'}), 404

    location = result[0]
    print(f"User's location: {location}")

    # Combine query and location
    combined_query = f"{query} near {location}"

    print(combined_query)

    # Call OpenAI API in a loop
    openai.api_key = OPENAI_KEY
    max_attempts = 10
    attempts = 0
    bar_found = False
    bar_dict = None

    while not bar_found and attempts < max_attempts:
        attempts += 1
        response = openai.chat.completions.create(
            model="ft:gpt-4o-2024-08-06:personal::A3BdX0ph",
            messages=[
                {"role": "system", "content": "You are a nightlife guru who recommends bars and clubs based on users' desires."},
                {"role": "user", "content": f"{combined_query}"}
            ],
            max_tokens=150
        )

        suggestions = response.choices[0].message.content
        suggested_bars = [suggestion.strip() for suggestion in suggestions.split(',') if suggestion.strip()]

        print("Pre database check:", suggested_bars)

        # Use ILIKE for case-insensitive matching and pattern matching for partial matches
        placeholder = ', '.join(['%s'] * len(suggested_bars))
        cursor.execute(f'SELECT * FROM bars WHERE name ILIKE ANY(ARRAY[{placeholder}]) LIMIT 1', tuple([f'%{bar}%' for bar in suggested_bars]))
        bars = cursor.fetchall()

        if bars:
            bar = bars[0]
            bar_dict = {
                "id": bar[0],
                "name": bar[1],
                "address": bar[2],
                "phone_number": bar[3],
                "description": bar[6],
                "vibe": float(bar[7]) if bar[7] is not None else None,
                "type": bar[8],
                "lineWaitTime": bar[9],
                "price_signs": bar[15],
                "price_num": bar[16],
                "photo": bar[18],
                "avgMaleAge": bar[10],
                "avgFemaleAge": bar[11],
                "percentSingleMen": bar[12],
                "percentSingleWomen": bar[13],
                "latitude": bar[23], 
                "longitude": bar[24],
                "djsInstagram": bar[25],
                "ticketLink": bar[26],
                "enableRequests": bar[27],
                "website_link": bar[28],
                "reservation_link": bar[29],
                "howCrowded": bar[30]
            }
            bar_found = True

    if not bar_found:
        cursor.close()
        conn.close()
        return jsonify({'error': 'This happens sometimes, try a different query'}), 400

    # Update the AI recommendations database
    import time
    current_time = int(time.time())
    cursor.execute('''
        INSERT INTO ai_recommendations (query, bars, last_updated)
        VALUES (%s, %s, %s)
        ON CONFLICT (query) DO UPDATE
        SET bars = EXCLUDED.bars,
            last_updated = EXCLUDED.last_updated
    ''', (query, json.dumps([bar_dict]), current_time))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify([bar_dict])





@app.route('/api/filter_bars', methods=['POST'])
def filter_bars():
    data = request.json or {}
    prices        = data.get('prices', [])            # e.g. ["$$", "$$$"]
    genres        = data.get('genres', [])            # e.g. ["Latin/Reggaeton","Disco/Funk"]
    vibes         = data.get('vibes', [])             # e.g. ["Upscale","Loungey"]
    neighborhoods = data.get('neighborhoods', [])     # e.g. ["SoHo","East Village"]

    # Build base query
    query = "SELECT * FROM bars WHERE 1=1"
    params = []

    # Price filter (any one match)
    if prices:
        placeholders = ','.join(['%s'] * len(prices))
        query += f" AND price_signs IN ({placeholders})"
        params.extend(prices)

    # Neighborhood filter (any one match)
    if neighborhoods:
        placeholders = ','.join(['%s'] * len(neighborhoods))
        query += f" AND neighborhood IN ({placeholders})"
        params.extend(neighborhoods)

    # Music-genre filter (match any one)
    if genres:
        genre_conditions = " OR ".join("%s = ANY(music_genres)" for _ in genres)
        query += f" AND ({genre_conditions})"
        params.extend(genres)

    # Vibes filter (match any one)
    if vibes:
        vibe_conditions = " OR ".join("%s = ANY(club_vibes)" for _ in vibes)
        query += f" AND ({vibe_conditions})"
        params.extend(vibes)

    # Final ordering
    query += " ORDER BY name"

    # Execute and fetch
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query, params)
    bars = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify(bars), 200

# SUBMIT NEARBY FORM

@app.route('/api/update_bar', methods=['POST'])
def update_bar():
    data = request.json
    bar_id = data.get('bar_id')
    vibe = data.get('vibe')
    line_length = data.get('line_length')
    how_crowded = data.get('how_crowded')
    bouncer_difficulty = data.get('bouncer_difficulty')
    ratio = data.get('ratio')

    if not bar_id or line_length is None:
        return jsonify({'error': 'bar_id and line_length are required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if vibe is not None:
        updates.append('vibe = %s')
        params.append(vibe)

    if how_crowded is not None:
        updates.append('how_crowded = %s')
        params.append(how_crowded)

    if bouncer_difficulty is not None:
        updates.append('bouncer_difficulty = %s')
        params.append(bouncer_difficulty)

    if ratio is not None:
        updates.append('ratio = %s')
        params.append(ratio)

    updates.append('line_wait_time = %s')
    params.append(line_length)

    update_query = f"UPDATE bars SET {', '.join(updates)} WHERE id = %s"
    params.append(bar_id)

    cursor.execute(update_query, params)
    conn.commit()

    cursor.close()
    conn.close()

    return jsonify({'status': 'Bar information updated successfully!'}), 200




# USERNAME VALIDATION AND SETTING

@app.route('/check_username', methods=['POST'])
def check_username():
    data = request.json
    username = data.get('username')

    if not username:
        return jsonify({'status': 'Username is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
    existing_user = cursor.fetchone()

    cursor.close()
    conn.close()

    if existing_user:
        return jsonify({'status': 'Username is already taken'}), 400
    else:
        return jsonify({'status': 'Username is available'}), 200










# BAR OWNER SPOTIFY STUFF



@app.route('/api/spotify_token', methods=['POST'])
def save_spotify_token():
    data = request.json
    token = data.get('token')
    bar_id = data.get('bar_id')

    if not token or not bar_id:
        return 'Missing token or bar_id', 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE bars SET spotify_token = %s WHERE id = %s', (token, bar_id))
    conn.commit()
    cursor.close()
    conn.close()

    return 'Token saved successfully', 200


@app.route('/api/spotify_callback')
def spotify_callback():
    code = request.args.get('code')
    if code is None:
        return 'Authorization code not provided', 400

    token_url = 'https://accounts.spotify.com/api/token'
    body_params = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    response = requests.post(token_url, data=body_params, headers=headers)
    
    if response.status_code != 200:
        return 'Failed to fetch token', 400

    token_data = response.json()
    access_token = token_data['access_token']

    bar_id = request.args.get('bar_id')
    if not bar_id:
        return 'Bar ID not provided', 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE bars SET spotify_token = %s WHERE id = %s', (access_token, bar_id))
    conn.commit()
    cursor.close()
    conn.close()

    return 'Spotify token saved successfully', 200


@app.route('/api/bars/<int:bar_id>/playlists')
def fetch_playlists(bar_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT spotify_token FROM bars WHERE id = %s', (bar_id,))
    bar = cursor.fetchone()
    cursor.close()
    conn.close()

    if not bar or not bar['spotify_token']:
        return jsonify({'error': 'Spotify token not found'}), 400

    spotify_token = bar['spotify_token']
    headers = {
        'Authorization': f'Bearer {spotify_token}'
    }
    response = requests.get('https://api.spotify.com/v1/me/playlists', headers=headers)

    if response.status_code != 200:
        return jsonify({'error': 'Failed to fetch playlists'}), 400

    playlists = response.json().get('items', [])
    conn = get_db_connection()
    cursor = conn.cursor()

    # Clear existing songs for the bar
    cursor.execute('DELETE FROM songs WHERE bar_id = %s', (bar_id,))
    
    # Clear existing playlists for the bar
    cursor.execute('DELETE FROM playlists WHERE bar_id = %s', (bar_id,))

    for playlist in playlists:
        cursor.execute('''
            INSERT INTO playlists (bar_id, name, spotify_id) VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
        ''', (bar_id, playlist['name'], playlist['id']))

    conn.commit()
    cursor.close()
    conn.close()
    print(playlists)
    return jsonify(response.json().get('items', []))








@app.route('/api/bars/<int:bar_id>/playlists/<string:playlist_id>/songs')
def fetch_songs(bar_id, playlist_id):
    print(f"Fetching songs for playlist_id: {playlist_id} and bar_id: {bar_id}")

    # Connect to the database
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if the playlist exists using spotify_id
    cursor.execute('SELECT id FROM playlists WHERE spotify_id = %s', (playlist_id,))
    playlist = cursor.fetchone()
    print(f"Database result for playlist: {playlist}")

    if not playlist:
        print(f"Playlist with ID {playlist_id} not found.")
        cursor.close()
        conn.close()
        return jsonify({'error': 'Playlist not found'}), 404

    # Get the internal playlist id
    internal_playlist_id = playlist[0]

    # Fetch the Spotify token for the bar
    cursor.execute('SELECT spotify_token FROM bars WHERE id = %s', (bar_id,))
    bar = cursor.fetchone()
    print(f"Database result for bar: {bar}")

    # Check if the token is found
    if not bar or not bar[0]:
        print("Spotify token not found for the bar.")
        cursor.close()
        conn.close()
        return jsonify({'error': 'Spotify token not found'}), 400

    spotify_token = bar[0]
    print(f"Using Spotify token: {spotify_token}")

    # Use the token to fetch songs from the Spotify API
    headers = {
        'Authorization': f'Bearer {spotify_token}'
    }
    response = requests.get(f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks', headers=headers)

    # Check the response status
    if response.status_code != 200:
        print(f"Failed to fetch songs, status code: {response.status_code}")
        cursor.close()
        conn.close()
        return jsonify({'error': 'Failed to fetch songs'}), 400

    tracks = response.json().get('items', [])
    print(f"Tracks fetched: {tracks}")

    # Prepare the songs list
    songs = []
    for item in tracks:
        track = item['track']
        song = {
            'id': track['id'],
            'name': track['name'],
            'artist': ', '.join(artist['name'] for artist in track['artists']),
            'albumArt': track['album']['images'][0]['url'] if track['album']['images'] else None,
            'spotify_url': track['external_urls']['spotify']
        }
        print(f"Song fetched: {song}")
        songs.append(song)

    # Clear existing songs for the bar's current playlist
    cursor.execute('DELETE FROM songs WHERE bar_id = %s', (bar_id,))
    
    # Insert or update the songs in the database using the internal playlist id and bar_id
    for song in songs:
        cursor.execute('''
            INSERT INTO songs (playlist_id, name, artist, album_art, spotify_url, bar_id) 
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET 
            name = EXCLUDED.name, 
            artist = EXCLUDED.artist, 
            album_art = EXCLUDED.album_art,
            spotify_url = EXCLUDED.spotify_url,
            bar_id = EXCLUDED.bar_id
        ''', (internal_playlist_id, song['name'], song['artist'], song['albumArt'], song['spotify_url'], bar_id))
        print(f"Inserted/Updated song: {song}")

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify(songs)











@app.route('/api/bars/<int:bar_id>/songs')
def get_songs_for_bar(bar_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.id, s.name, s.artist, s.album_art, s.spotify_url
        FROM songs s
        WHERE s.bar_id = %s
    ''', (bar_id,))
    songs = cursor.fetchall()
    cursor.close()
    conn.close()

    song_list = [
        {
            'id': str(song[0]),  # Convert id to string
            'name': song[1],
            'artist': song[2],
            'albumArt': song[3],
            'spotifyUrl': song[4]  # Include the spotifyUrl in the response
        }
        for song in songs
    ]
    
    return jsonify(song_list)



















# USER ONBOARDING STUFF


@app.route('/create_user', methods=['POST'])
def create_user():
    data = request.json
    identifier = data['email']
    name = data.get('name')  # Get the name from the request

    if not name:
        return jsonify({"error": "Name is required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if the user already exists
    cursor.execute('SELECT * FROM users WHERE email = %s', (identifier,))
    user = cursor.fetchone()

    if user:
        return jsonify({'status': 'User already exists'}), 200

    cursor.execute(
        'INSERT INTO users (email, name) VALUES (%s, %s)',
        (identifier, name)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"status": "User created successfully!"}), 201





@app.route('/check_user_birthday', methods=['POST'])
def check_user_birthday():
    data = request.json
    email = data['email']
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT birthday, gender, relationship_status FROM users WHERE email = %s', (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if user and user['birthday'] and user['gender'] and user['relationship_status']:
        return jsonify({'has_info': True}), 200
    else:
        return jsonify({'has_info': False}), 200



@app.route('/update_user', methods=['POST'])
def update_user():
    data = request.json
    email = data.get('email')
    birthday = data.get('birthday')
    gender = data.get('gender')
    relationship_status = data.get('relationship_status')
    location = data.get('location')
    latitude = data.get('latitude')
    longitude = data.get('longitude')
    username = data.get('username')

    if not email:
        return jsonify({'status': 'Email is required'}), 400
    
    print(f"Received data for update: email={email}, birthday={birthday}, gender={gender}, relationship_status={relationship_status}, location={location}, latitude={latitude}, longitude={longitude}, username={username}")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build the update query dynamically based on which fields are provided
    updates = []
    params = []

    if birthday:
        updates.append('birthday = %s')
        params.append(birthday)
    if gender:
        updates.append('gender = %s')
        params.append(gender)
    if relationship_status:
        updates.append('relationship_status = %s')
        params.append(relationship_status)
    if location:
        updates.append('location = %s')
        params.append(location)
    if latitude:
        updates.append('latitude = %s')
        params.append(latitude)
    if longitude:
        updates.append('longitude = %s')
        params.append(longitude)
    if username:
        updates.append('username = %s')
        params.append(username)

    if not updates:
        return jsonify({'status': 'No fields to update'}), 400

    update_query = f"UPDATE users SET {', '.join(updates)} WHERE email = %s"
    params.append(email)

    print(f"Executing query: {update_query} with params: {params}")
    cursor.execute(update_query, params)
    conn.commit()
    print("Commit successful")
    cursor.close()
    conn.close()
    return jsonify({'status': 'User information updated successfully!'}), 200





# DELETE USER

@app.route('/delete_user', methods=['POST'])
def delete_user():
    data = request.json
    email = data['email']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Start a transaction
        conn.autocommit = False

        # Delete from votes
        cursor.execute('DELETE FROM votes WHERE user_email = %s', (email,))

        # Delete from song_requests
        cursor.execute('DELETE FROM song_requests WHERE user_email = %s', (email,))

        # Delete from user_feedback
        cursor.execute('DELETE FROM user_feedback WHERE user_email = %s', (email,))

        # Get user_id from users table
        cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()
        if not user:
            raise Exception("User not found")
        user_id = user[0]

        # Delete from been_there
        cursor.execute('DELETE FROM been_there WHERE user_id = %s', (user_id,))

        # Delete from liked
        cursor.execute('DELETE FROM liked WHERE user_id = %s', (user_id,))

        # Delete from want_to_go
        cursor.execute('DELETE FROM want_to_go WHERE user_id = %s', (user_id,))

        # Finally, delete from users
        cursor.execute('DELETE FROM users WHERE email = %s', (email,))

        # Commit the transaction
        conn.commit()
        return jsonify({"status": "User deleted successfully!"}), 200

    except Exception as e:
        # Rollback the transaction on error
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()




@app.route('/api/bars', methods=['GET'])
def get_bars():
    page = int(request.args.get('page', 1))
    per_page = 30
    #per_page = int(request.args.get('per_page', 30))
    search = request.args.get('search', '')
    selected_price = request.args.get('selected_price', '')
    selected_distance = float(request.args.get('selected_distance', 0))
    latitude = float(request.args.get('latitude', 0))
    longitude = float(request.args.get('longitude', 0))
    selected_genres = request.args.getlist('selected_genres[]')

    offset = (page - 1) * per_page

    # Haversine formula to calculate distance
    haversine = """
    3959 * acos(
        cos(radians(%s)) * cos(radians(b.latitude)) *
        cos(radians(b.longitude) - radians(%s)) +
        sin(radians(%s)) * sin(radians(b.latitude))
    )
    """

    query = f"""
    SELECT b.*, {haversine} AS distance
    FROM bars b
    LEFT JOIN playlists p ON b.id = p.bar_id
    LEFT JOIN songs s ON p.id = s.playlist_id
    WHERE (b.name ILIKE %s OR b.description ILIKE %s OR b.address ILIKE %s
           OR s.name ILIKE %s OR s.artist ILIKE %s)
    AND (%s = '' OR b.price_signs = %s)
    AND (%s = 0 OR {haversine} < %s)
    """

    params = [
        latitude, longitude, latitude,
        f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%",
        selected_price, selected_price,
        selected_distance, latitude, longitude, latitude, selected_distance
    ]

    if selected_genres:
        genre_conditions = " OR ".join("b.venue_types ILIKE %s" for _ in selected_genres)
        query += f" AND ({genre_conditions})"
        params.extend(f"%{genre}%" for genre in selected_genres)

    query += f" ORDER BY distance LIMIT %s OFFSET %s"
    params.extend([per_page, offset])

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query, params)
    bars = cursor.fetchall()

    # Fetch songs for each bar
    for bar in bars:
        bar_id = bar['id']
        cursor.execute("""
            SELECT s.id::text, s.name, s.artist, s.album_art, s.spotify_url
            FROM songs s
            JOIN playlists p ON s.playlist_id = p.id
            WHERE p.bar_id = %s
        """, (bar_id,))
        songs = cursor.fetchall()
        bar['songs'] = songs

    cursor.close()
    conn.close()

    return jsonify(bars)






@app.route('/api/nearby_bars', methods=['GET'])
def get_nearby_bars():
    latitude = float(request.args.get('latitude'))
    longitude = float(request.args.get('longitude'))
    distance_limit = 15  # meters

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371e3  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    query = """
    SELECT id, name, address, latitude, longitude
    FROM bars
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query)
    bars = cursor.fetchall()

    nearby_bars = []
    for bar in bars:
        if bar['latitude'] is None or bar['longitude'] is None:
            continue
        distance = haversine(latitude, longitude, bar['latitude'], bar['longitude'])
        if distance < distance_limit:
            bar['distance'] = distance
            nearby_bars.append(bar)

    # Sort by distance
    nearby_bars.sort(key=lambda x: x['distance'])

    cursor.close()
    conn.close()

    print(nearby_bars)

    return jsonify(nearby_bars)






@app.route('/api/bars/<int:id>', methods=['GET'])
def get_bar(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM bars WHERE id = %s', (id,))
    bar = cursor.fetchone()
    
    if bar:
        cursor.execute('SELECT * FROM events WHERE bar_id = %s', (id,))
        events = cursor.fetchall()
        bar['events'] = events

    cursor.close()
    conn.close()

    if bar:
        return jsonify(bar)
    return jsonify({"error": "Bar not found"}), 404



@app.route('/api/events', methods=['GET'])
def get_events():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM events')
    events = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(events)

@app.route('/api/events/<int:id>', methods=['GET'])
def get_event(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM events WHERE id = %s', (id,))
    event = cursor.fetchone()
    cursor.close()
    conn.close()
    if event:
        return jsonify(event)
    return jsonify({"error": "Event not found"}), 404

@app.route('/api/bars/<int:id>/events', methods=['GET'])
def get_bar_events(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM events WHERE bar_id = %s', (id,))
    events = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(events)



@app.route('/api/song_requests', methods=['POST'])
def create_song_request():
    data = request.json
    user_email = data['user_email']
    bar_id = data['bar_id']
    event_id = data.get('event_id', None)  # Optional event_id
    song_name = data['song_name']
    artist_name = data['artist_name']
    album_cover_url = data.get('album_cover_url', '')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if the song has already been requested by this user at this bar/event
    cursor.execute(
        'SELECT * FROM song_requests WHERE user_email = %s AND bar_id = %s AND (event_id = %s OR event_id IS NULL) AND song_name = %s AND artist_name = %s',
        (user_email, bar_id, event_id, song_name, artist_name)
    )
    existing_request = cursor.fetchone()

    if existing_request:
        cursor.close()
        conn.close()
        return jsonify({"status": "Song has already been requested"}), 400

    cursor.execute(
        'INSERT INTO song_requests (user_email, bar_id, event_id, song_name, artist_name, album_cover_url) VALUES (%s, %s, %s, %s, %s, %s)',
        (user_email, bar_id, event_id, song_name, artist_name, album_cover_url)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"status": "Song request created successfully!"}), 201




@app.route('/api/song_requests/<int:bar_id>', methods=['GET'])
def get_song_requests(bar_id):
    event_id = request.args.get('event_id', None)
    today = date.today()

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    if event_id:
        # Get song requests for the specific event
        cursor.execute('SELECT * FROM song_requests WHERE bar_id = %s AND (event_id = %s OR (event_id IS NULL AND request_time::date = %s)) ORDER BY request_time DESC', (bar_id, event_id, today))
    else:
        # Get song requests for the bar (not tied to an event)
        cursor.execute('SELECT * FROM song_requests WHERE bar_id = %s AND event_id IS NULL ORDER BY request_time DESC', (bar_id,))

    song_requests = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(song_requests)

@app.route('/api/song_requests/<int:request_id>/upvote', methods=['POST'])
def upvote_song_request(request_id):
    data = request.json
    user_email = data['user_email']

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if the user has already voted on this request
    cursor.execute('SELECT * FROM votes WHERE user_email = %s AND request_id = %s', (user_email, request_id))
    existing_vote = cursor.fetchone()

    if existing_vote:
        cursor.close()
        conn.close()
        return jsonify({"status": "User has already voted on this request"}), 400

    # Check if the user is the one who requested the song
    cursor.execute('SELECT user_email FROM song_requests WHERE id = %s', (request_id,))
    request = cursor.fetchone()

    if request and request['user_email'] == user_email:
        cursor.close()
        conn.close()
        return jsonify({"status": "User cannot vote on their own request"}), 400

    cursor.execute('UPDATE song_requests SET upvotes = upvotes + 1 WHERE id = %s', (request_id,))
    cursor.execute('INSERT INTO votes (user_email, request_id, vote_type) VALUES (%s, %s, %s)', (user_email, request_id, 'upvote'))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "Upvoted successfully!"})

@app.route('/api/song_requests/<int:request_id>/downvote', methods=['POST'])
def downvote_song_request(request_id):
    data = request.json
    user_email = data['user_email']

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if the user has already voted on this request
    cursor.execute('SELECT * FROM votes WHERE user_email = %s AND request_id = %s', (user_email, request_id))
    existing_vote = cursor.fetchone()

    if existing_vote:
        cursor.close()
        conn.close()
        return jsonify({"status": "User has already voted on this request"}), 400

    # Check if the user is the one who requested the song
    cursor.execute('SELECT user_email FROM song_requests WHERE id = %s', (request_id,))
    request = cursor.fetchone()

    if request and request['user_email'] == user_email:
        cursor.close()
        conn.close()
        return jsonify({"status": "User cannot vote on their own request"}), 400

    cursor.execute('UPDATE song_requests SET downvotes = downvotes + 1 WHERE id = %s', (request_id,))
    cursor.execute('INSERT INTO votes (user_email, request_id, vote_type) VALUES (%s, %s, %s)', (user_email, request_id, 'downvote'))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "Downvoted successfully!"})






# BEEN THERE, LIKED, WANT TO GO



@app.route('/add_to_list', methods=['POST'])
def add_to_list():
    data = request.json
    email = data.get('email')
    bar_id = data.get('bar_id')
    list_type = data.get('list_type')
    rating = data.get('rating')
    comments = data.get('comments')

    if not email or not bar_id or not list_type:
        return jsonify({'status': 'Email, Bar ID, and List Type are required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user ID from email
    cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
    user = cursor.fetchone()

    if not user:
        return jsonify({'status': 'User not found'}), 404

    user_id = user['id']

    # Check if the bar already exists in the list
    if list_type == 'been_there':
        cursor.execute(
            'SELECT * FROM been_there WHERE user_id = %s AND bar_id = %s',
            (user_id, bar_id)
        )
    elif list_type == 'liked':
        cursor.execute(
            'SELECT * FROM liked WHERE user_id = %s AND bar_id = %s',
            (user_id, bar_id)
        )
    elif list_type == 'want_to_go':
        cursor.execute(
            'SELECT * FROM want_to_go WHERE user_id = %s AND bar_id = %s',
            (user_id, bar_id)
        )
    else:
        return jsonify({'status': 'Invalid list type'}), 400

    existing_entry = cursor.fetchone()

    if existing_entry:
        return jsonify({'status': f'This bar is already in your {list_type} list'}), 400

    # Insert into the appropriate list table
    if list_type == 'been_there':
        cursor.execute(
            'INSERT INTO been_there (user_id, bar_id, rating, comments) VALUES (%s, %s, %s, %s)',
            (user_id, bar_id, rating, comments)
        )
    elif list_type == 'liked':
        cursor.execute(
            'INSERT INTO liked (user_id, bar_id) VALUES (%s, %s)',
            (user_id, bar_id)
        )
    elif list_type == 'want_to_go':
        cursor.execute(
            'INSERT INTO want_to_go (user_id, bar_id) VALUES (%s, %s)',
            (user_id, bar_id)
        )

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': f'Added to {list_type} list successfully!'}), 200




@app.route('/get_been_there', methods=['GET'])
def get_been_there():
    email = request.args.get('email')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('''
        SELECT b.id, b.name, b.address, b.photo, bt.rating, bt.comments
        FROM been_there bt
        JOIN bars b ON bt.bar_id = b.id
        JOIN users u ON bt.user_id = u.id
        WHERE u.email = %s
        ORDER BY bt.rating DESC
    ''', (email,))
    
    bars = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify(bars)



@app.route('/get_liked', methods=['GET'])
def get_liked():
    email = request.args.get('email')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('''
        SELECT b.id, b.name, b.address, b.photo
        FROM liked l
        JOIN bars b ON l.bar_id = b.id
        JOIN users u ON l.user_id = u.id
        WHERE u.email = %s
    ''', (email,))
    
    bars = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify(bars)


@app.route('/get_want_to_go', methods=['GET'])
def get_want_to_go():
    email = request.args.get('email')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('''
        SELECT b.id, b.name, b.address, b.photo
        FROM want_to_go wtg
        JOIN bars b ON wtg.bar_id = b.id
        JOIN users u ON wtg.user_id = u.id
        WHERE u.email = %s
    ''', (email,))
    
    bars = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify(bars)



@app.route('/remove_from_list', methods=['POST'])
def remove_from_list():
    data = request.json
    email = data.get('email')
    bar_id = data.get('bar_id')
    list_type = data.get('list_type')

    if not email or not bar_id or not list_type:
        return jsonify({'status': 'Missing required fields'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user_id from email
    cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
    user_id = cursor.fetchone()

    if not user_id:
        return jsonify({'status': 'User not found'}), 404


    if list_type == 'been_there':
        cursor.execute('DELETE FROM been_there WHERE user_id = %s AND bar_id = %s', (user_id, bar_id))
    elif list_type == 'liked':
        cursor.execute('DELETE FROM liked WHERE user_id = %s AND bar_id = %s', (user_id, bar_id))
    elif list_type == 'want_to_go':
        cursor.execute('DELETE FROM want_to_go WHERE user_id = %s AND bar_id = %s', (user_id, bar_id))
    else:
        return jsonify({'status': 'Invalid list type'}), 400

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'status': 'Bar removed from list successfully!'}), 200



# BAR OWNER SIDE

@app.route('/api/owned_bars', methods=['GET'])
def get_owned_bars():
    email = request.args.get('email')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM bars WHERE owner_email = %s', (email,))
    bars = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(bars)


@app.route('/validate_passcode', methods=['POST'])
def validate_passcode():
    data = request.json
    bar_id = data['bar_id']
    passcode = data['passcode']
    user_email = data['user_email']  # Get the user email from the request data
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM bars WHERE id = %s AND passcode = %s', (bar_id, passcode))
    bar = cursor.fetchone()
    
    if bar:
        cursor.execute('UPDATE bars SET owner_email = %s WHERE id = %s', (user_email, bar_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'Passcode is valid', 'bar': bar}), 200
    else:
        cursor.close()
        conn.close()
        return jsonify({'status': 'Passcode is invalid'}), 400





# UPDATE BAR DETAILS


@app.route('/api/update_bar_detail/<int:bar_id>', methods=['PUT'])
def update_bar_detail(bar_id):
    data = request.json
    field_map = {
        "description": "description",
        "phone_number": "phone_number",
        "djs_instagram": "djs_instagram",
        "ticket_link": "ticket_link",
        "price_num": "price_num"
    }
    
    updates = []
    params = []
    
    for key, field in field_map.items():
        if key in data:
            if data[key] == "" or data[key] is None:
                updates.append(f"{field} = NULL")
            else:
                if key == "price_num":
                    try:
                        # Validate that price_num is a valid double precision number
                        price_num = float(data[key])
                        updates.append(f"{field} = %s")
                        params.append(price_num)
                    except ValueError:
                        return jsonify({"error": f"Invalid value for {key}"}), 400
                else:
                    updates.append(f"{field} = %s")
                    params.append(data[key])
    
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    
    query = f"UPDATE bars SET {', '.join(updates)} WHERE id = %s"
    params.append(bar_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, tuple(params))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({"status": "Bar details updated successfully"})



@app.route('/api/update_enable_requests/<int:bar_id>', methods=['PUT'])
def update_enable_requests(bar_id):
    data = request.json
    enable_requests = data.get('enable_requests')

    if enable_requests is None:
        return jsonify({'error': 'enable_requests field is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE bars SET enable_requests = %s WHERE id = %s', (enable_requests, bar_id))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'enable_requests updated successfully'})








# FEED VIEW FEEDBACK

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    data = request.json
    email = data.get('email')
    feedback = data.get('feedback')
    comment = data.get('comment')

    if not email or feedback not in ['yes', 'neutral', 'no']:
        return jsonify({'error': 'Invalid input'}), 400

    yes = 1 if feedback == 'yes' else 0
    neutral = 1 if feedback == 'neutral' else 0
    no = 1 if feedback == 'no' else 0

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO user_feedback (user_email, yes, neutral, no, comment)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_email) 
        DO UPDATE SET 
            yes = EXCLUDED.yes,
            neutral = EXCLUDED.neutral,
            no = EXCLUDED.no,
            comment = EXCLUDED.comment,
            created_at = CURRENT_TIMESTAMP
    ''', (email, yes, neutral, no, comment))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'Feedback submitted successfully'}), 200

@app.route('/api/feedback/<email>', methods=['GET'])
def get_feedback(email):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM user_feedback WHERE user_email = %s', (email,))
    feedback = cursor.fetchone()
    cursor.close()
    conn.close()

    if feedback:
        return jsonify(feedback)
    return jsonify({'error': 'Feedback not found'}), 404



# SOCIAL PART

# ADD FRIENDS

@app.route('/api/users', methods=['GET'])
def get_users():
    search = request.args.get('search', '').strip()
    identifier = request.args.get('identifier').strip()
    
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    # If the search query is empty, return an empty list
    if not search:
        return jsonify([])

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get the user ID of the current user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        return jsonify({'status': 'User not found'}), 404
    user_id = result['id']

    # Modify the query to exclude the current user and users who have blocked the current user
    query = """
    SELECT id, email, name, username FROM users
    WHERE (name ILIKE %s OR username ILIKE %s)
    AND id != %s  -- Exclude the current user
    AND id NOT IN (
        SELECT blocker_id FROM blocks WHERE blocked_id = %s
    )
    ORDER BY name ASC
    """
    
    params = [f"%{search}%", f"%{search}%", user_id, user_id]
    
    cursor.execute(query, params)
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify(users)


# FOLLOWERS FOLLOWERS

@app.route('/api/followers', methods=['GET'])
def get_followers():
    identifier = request.args.get('identifier')
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result['id']

    # Fetch followers and their details
    cursor.execute("""
        SELECT u.id, u.email, u.name, u.username
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.followed_id = %s
        ORDER BY u.name ASC
    """, (user_id,))
    
    followers = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(followers)

# FOLLOWING FOLLOWING

@app.route('/api/following_users', methods=['GET'])
def get_following_users():
    identifier = request.args.get('identifier')
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result['id']

    # Fetch users the current user is following
    cursor.execute("""
        SELECT u.id, u.email, u.name, u.username
        FROM follows f
        JOIN users u ON u.id = f.followed_id
        WHERE f.follower_id = %s
        ORDER BY u.name ASC
    """, (user_id,))
    
    following_users = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(following_users)


# NOTIFICATION FOLLOWERS

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    identifier = request.args.get('identifier')
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result['id']

    # Fetch recent followers and their details
    cursor.execute("""
        SELECT u.id, u.email, u.name, u.username
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.followed_id = %s
        ORDER BY f.created_at DESC
    """, (user_id,))
    
    notifications = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(notifications)


# FOLLOW

@app.route('/api/follow', methods=['POST'])
def follow_user():
    data = request.json
    user_identifier = data.get('identifier')  # The identifier of the user who is performing the follow action
    followed_id = data.get('followed_id')  # The ID of the user being followed

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get the user ID of the follower using the identifier (email)
    cursor.execute("SELECT id FROM users WHERE email = %s", (user_identifier,))
    follower_id = cursor.fetchone()

    if follower_id is None:
        return jsonify({'error': 'Follower not found'}), 404

    # Insert the follow relationship into the follows table
    cursor.execute(
        "INSERT INTO follows (follower_id, followed_id) VALUES (%s, %s)",
        (follower_id, followed_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Followed successfully'}), 201


# UNFOLLOW

@app.route('/api/unfollow', methods=['POST'])
def unfollow_user():
    data = request.json
    user_identifier = data.get('identifier')  # The identifier of the user who is performing the unfollow action
    followed_id = data.get('followed_id')  # The ID of the user being unfollowed

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get the user ID of the follower using the identifier (email)
    cursor.execute("SELECT id FROM users WHERE email = %s", (user_identifier,))
    follower_id = cursor.fetchone()

    if follower_id is None:
        return jsonify({'error': 'Follower not found'}), 404

    # Delete the follow relationship from the follows table
    cursor.execute(
        "DELETE FROM follows WHERE follower_id = %s AND followed_id = %s",
        (follower_id, followed_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Unfollowed successfully'}), 200

# KEEP FOLLOWING

@app.route('/api/following', methods=['GET'])
def get_following():
    user_identifier = request.args.get('identifier')

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get the user ID of the follower using the identifier (email)
    cursor.execute("SELECT id FROM users WHERE email = %s", (user_identifier,))
    follower_data = cursor.fetchone()

    if follower_data is None:
        cursor.close()
        conn.close()
        return jsonify([])

    follower_id = follower_data['id']  # Extract the 'id' from the dictionary

    # Get the list of followed user IDs
    cursor.execute("SELECT followed_id FROM follows WHERE follower_id = %s", (follower_id,))
    followed_ids = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify([follow['followed_id'] for follow in followed_ids])


# BEEN THERE FEED

@app.route('/api/following_been_there', methods=['GET'])
def get_following_been_there():
    user_identifier = request.args.get('identifier')

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get the user ID of the follower using the identifier (email)
    cursor.execute("SELECT id FROM users WHERE email = %s", (user_identifier,))
    follower_data = cursor.fetchone()

    if follower_data is None:
        cursor.close()
        conn.close()
        return jsonify([])

    follower_id = follower_data['id']

    # Get the list of followed user IDs
    cursor.execute("SELECT followed_id FROM follows WHERE follower_id = %s", (follower_id,))
    followed_ids = cursor.fetchall()

    if not followed_ids:
        cursor.close()
        conn.close()
        return jsonify([])

    # Extract the followed_ids
    followed_ids = [follow['followed_id'] for follow in followed_ids]

    # Get "Been There" entries for followed users
    query = """
    SELECT bt.id, bt.user_id, bt.bar_id, bt.rating, bt.comments, u.name as user_name, b.name as bar_name 
    FROM been_there bt
    JOIN users u ON bt.user_id = u.id
    JOIN bars b ON bt.bar_id = b.id
    WHERE bt.user_id = ANY(%s)
    ORDER BY bt.id DESC
    """
    cursor.execute(query, (followed_ids,))
    been_there_entries = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(been_there_entries)


# FRIENDS LOCATIONS

@app.route('/api/mutual_friends', methods=['GET'])
def get_mutual_friends():
    identifier = request.args.get('identifier')
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result['id']

    # Get users that the requesting user follows
    cursor.execute("SELECT followed_id FROM follows WHERE follower_id = %s", (user_id,))
    following = cursor.fetchall()

    following_ids = {f['followed_id'] for f in following}

    # Get users that follow the requesting user
    cursor.execute("SELECT follower_id FROM follows WHERE followed_id = %s", (user_id,))
    followers = cursor.fetchall()

    follower_ids = {f['follower_id'] for f in followers}

    # Find mutual friends (intersection of following and followers)
    mutual_ids = following_ids.intersection(follower_ids)

    if not mutual_ids:
        cursor.close()
        conn.close()
        return jsonify([])  # Return empty list if no mutual friends found

    # Fetch mutual friends' details including is_sharing_location
    format_strings = ','.join(['%s'] * len(mutual_ids))
    cursor.execute(f"""
        SELECT u.id, u.email, u.name, u.username, u.latitude, u.longitude, f.is_sharing_location
        FROM users u
        JOIN follows f ON u.id = f.followed_id
        WHERE u.id IN ({format_strings}) AND f.follower_id = %s
    """, tuple(mutual_ids) + (user_id,))

    mutual_friends = cursor.fetchall()

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371e3  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    # For each mutual friend, find the closest bar if they are sharing location
    for friend in mutual_friends:
        cursor.execute("""
            SELECT is_sharing_location FROM follows
            WHERE follower_id = %s AND followed_id = %s
        """, (friend['id'], user_id))
        sharing_status = cursor.fetchone()
        if not (sharing_status and sharing_status['is_sharing_location']):
            friend['latitude'] = None
            friend['longitude'] = None
            friend['current_bar'] = None
        else:
            cursor.execute("SELECT id, name, latitude, longitude FROM bars")
            bars = cursor.fetchall()

            closest_bar = None
            min_distance = float('inf')
            if friend['latitude'] and friend['longitude']:
                for bar in bars:
                    distance = haversine(friend['latitude'], friend['longitude'], bar['latitude'], bar['longitude'])
                    if distance < min_distance:
                        min_distance = distance
                        closest_bar = bar['name'] if distance < 15 else None  # Assuming a 15 meter threshold

            friend['current_bar'] = closest_bar

    cursor.close()
    conn.close()

    print(mutual_friends)

    return jsonify(mutual_friends)





@app.route('/api/update_sharing', methods=['POST'])
def update_sharing():
    data = request.json
    identifier = data.get('identifier')
    friend_id = data.get('friend_id')
    is_sharing_location = data.get('is_sharing_location')

    # Debug: Print received data
    print(f"Received data: identifier={identifier}, friend_id={friend_id}, is_sharing_location={is_sharing_location}")

    if not identifier or not friend_id or is_sharing_location is None:
        print("Invalid request data")
        return jsonify({'status': 'Invalid request data'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()

    if result is None:
        print("User not found")
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result[0]  # Accessing the first element of the tuple

    # Debug: Print user_id
    print(f"User ID for identifier {identifier} is {user_id}")

    # Check if the follow relationship exists
    cursor.execute(
        "SELECT * FROM follows WHERE follower_id = %s AND followed_id = %s",
        (user_id, friend_id)
    )
    follow_exists = cursor.fetchone()

    if follow_exists is None:
        print(f"No follow relationship exists between user_id {user_id} and friend_id {friend_id}")
        cursor.close()
        conn.close()
        return jsonify({'status': 'Follow relationship not found'}), 404

    # Update the sharing location status in the follows table
    cursor.execute(
        "UPDATE follows SET is_sharing_location = %s WHERE follower_id = %s AND followed_id = %s",
        (is_sharing_location, user_id, friend_id)
    )

    conn.commit()
    cursor.close()
    conn.close()

    print("Location sharing status updated successfully")
    return jsonify({'status': 'Location sharing status updated successfully!'}), 200



# BLOCK

@app.route('/api/block', methods=['POST'])
def block_user():
    data = request.json
    identifier = data.get('identifier')
    blocked_id = data.get('blocked_id')

    if not identifier or not blocked_id:
        return jsonify({'status': 'Invalid request data'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if result is None:
        return jsonify({'status': 'User not found'}), 404

    blocker_id = result[0]

    # Insert into blocks table
    cursor.execute("""
        INSERT INTO blocks (blocker_id, blocked_id)
        VALUES (%s, %s)
        ON CONFLICT (blocker_id, blocked_id) DO NOTHING
    """, (blocker_id, blocked_id))

    # Unfollow each other
    cursor.execute("""
        DELETE FROM follows WHERE (follower_id = %s AND followed_id = %s)
        OR (follower_id = %s AND followed_id = %s)
    """, (blocker_id, blocked_id, blocked_id, blocker_id))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'User blocked successfully!'}), 200

@app.route('/api/unblock', methods=['POST'])
def unblock_user():
    data = request.json
    identifier = data.get('identifier')
    blocked_id = data.get('blocked_id')

    if not identifier or not blocked_id:
        return jsonify({'status': 'Invalid request data'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if result is None:
        return jsonify({'status': 'User not found'}), 404

    blocker_id = result[0]

    # Delete from blocks table
    cursor.execute("""
        DELETE FROM blocks WHERE blocker_id = %s AND blocked_id = %s
    """, (blocker_id, blocked_id))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'User unblocked successfully!'}), 200

@app.route('/api/blocked_users', methods=['GET'])
def get_blocked_users():
    identifier = request.args.get('identifier')
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user_id of the requesting user
    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    result = cursor.fetchone()
    if not result:
        cursor.close()
        conn.close()
        return jsonify({'status': 'User not found'}), 404

    user_id = result['id']

    # Get blocked users
    cursor.execute("""
        SELECT u.id, u.email, u.name, u.username
        FROM blocks b
        JOIN users u ON b.blocked_id = u.id
        WHERE b.blocker_id = %s
    """, (user_id,))
    blocked_users = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(blocked_users)

# FOLLOWER FOLLOWING COUNT

@app.route('/api/follow_counts', methods=['GET'])
def get_follow_counts():
    identifier = request.args.get('identifier')
    
    if not identifier:
        return jsonify({'status': 'Identifier is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE email = %s", (identifier,))
    user_id = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM follows WHERE followed_id = %s", (user_id,))
    follower_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM follows WHERE follower_id = %s", (user_id,))
    following_count = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return jsonify({
        'follower_count': follower_count,
        'following_count': following_count
    })


# FRIEND BEEN THERE

@app.route('/api/been_there', methods=['GET'])
def get_been_there_entries():
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'status': 'User ID is required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT bt.id, bt.user_id, bt.bar_id, bt.rating, bt.comments, u.name AS user_name, b.name AS bar_name
        FROM been_there bt
        JOIN users u ON bt.user_id = u.id
        JOIN bars b ON bt.bar_id = b.id
        WHERE bt.user_id = %s
    """, (user_id,))
    entries = cursor.fetchall()
    cursor.close()
    conn.close()

    print(entries)
    
    return jsonify(entries)


# FRIEND WANT TO GO

@app.route('/api/want_to_go', methods=['GET'])
def get_want_to_go_entries():
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'status': 'User ID is required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT wt.id, wt.user_id, wt.bar_id, u.name AS user_name, b.name AS bar_name
        FROM want_to_go wt
        JOIN users u ON wt.user_id = u.id
        JOIN bars b ON wt.bar_id = b.id
        WHERE wt.user_id = %s
    """, (user_id,))
    entries = cursor.fetchall()
    cursor.close()
    conn.close()

    print(entries)
    
    return jsonify(entries)

# RELATIVE RANKING

@app.route('/save_like_level', methods=['POST'])
def save_like_level():
    data = request.json
    email = data.get('email')
    bar_id = data.get('bar_id')
    like_level = data.get('like_level')
    rating = data.get('rating')  # Get the rank passed from the frontend

    if not email or not bar_id or not like_level or rating is None:
        return jsonify({'status': 'Email, Bar ID, Like Level, and Rating are required'}), 400

    # Get DB connection
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user ID from email
    cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
    user = cursor.fetchone()

    if not user:
        return jsonify({'status': 'User not found'}), 404

    user_id = user['id']

    # Check if an entry already exists for this user and bar
    cursor.execute(
        'SELECT * FROM been_there WHERE user_id = %s AND bar_id = %s',
        (user_id, bar_id)
    )
    existing_entry = cursor.fetchone()

    if existing_entry:
        # Update the existing entry
        cursor.execute(
            '''
            UPDATE been_there
            SET like_level = %s, rating = %s
            WHERE user_id = %s AND bar_id = %s
            ''',
            (like_level, rating, user_id, bar_id)
        )
    else:
        # Insert a new entry
        cursor.execute(
            '''
            INSERT INTO been_there (user_id, bar_id, like_level, rating)
            VALUES (%s, %s, %s, %s)
            ''',
            (user_id, bar_id, like_level, rating)
        )

    # Commit the transaction
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'Like level and rating saved successfully!'}), 200



@app.route('/get_bars_by_like_level', methods=['POST'])
def get_bars_by_like_level():
    data = request.json
    email = data.get('email')
    like_level = data.get('like_level')

    if not email or not like_level:
        return jsonify({'status': 'Email and Like Level are required'}), 400

    # Get DB connection
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get user ID from email
    cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
    user = cursor.fetchone()

    if not user:
        return jsonify({'status': 'User not found'}), 404

    user_id = user['id']

    # Get bars with the selected like_level
    cursor.execute(
        '''
        SELECT * FROM been_there
        JOIN bars ON been_there.bar_id = bars.id
        WHERE been_there.user_id = %s AND been_there.like_level = %s
        ORDER BY been_there.rating DESC
        ''',
        (user_id, like_level)
    )

    bars = cursor.fetchall()

    # Close connection
    cursor.close()
    conn.close()

    return jsonify({'status': 'Bars fetched successfully', 'bars': bars}), 200


@app.route('/validate_invite_code', methods=['POST'])
def validate_invite_code():
    data = request.json
    invite_code = data.get('invite_code')

    if not invite_code:
        return jsonify({"status": "error", "message": "Invite code is required"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Check if the invite code exists
        cur.execute("""
            SELECT code, expired, amount_used 
            FROM invite_codes 
            WHERE code = %s
        """, (invite_code,))
        result = cur.fetchone()

        if not result:
            return jsonify({"status": "error", "message": "Invalid invite code"}), 404

        code, expired, amount_used = result["code"], result["expired"], result["amount_used"]

        if expired:
            return jsonify({"status": "error", "message": "Invite code is expired"}), 400

        # Increment the usage count
        cur.execute("""
            UPDATE invite_codes
            SET amount_used = amount_used + 1
            WHERE code = %s
        """, (code,))
        conn.commit()

        cur.close()
        conn.close()

        return jsonify({"status": "success", "message": "Invite code validated successfully"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/api/latest_version', methods=['GET'])
def get_latest_version():
    return jsonify({
        'latest_version': '5.0',
        'minimum_supported_version': '4.5',
        'force_update': False  # Set to True if the update is mandatory
    })



if __name__ == '__main__':

    # Run the Flask application
    app.run(debug=True, host='0.0.0.0', port=5000)