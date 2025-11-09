from flask import Flask, request, jsonify, session, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId
import os
import uuid
import logging
import hashlib
import base64
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'gizli-anahtar-2024')

# Dosya yükleme ayarları
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {
    'image': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'],
    'video': ['mp4', 'avi', 'mov', 'mkv', 'webm'],
    'audio': ['mp3', 'wav', 'ogg', 'm4a', 'aac'],
    'document': ['pdf', 'doc', 'docx', 'txt', 'zip', 'rar']
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
    transport=['websocket', 'polling']
)

active_users = {}

# ADMIN E-POSTA - Bu e-postaya sahip kullanıcı admin olacak
ADMIN_EMAIL = "nesillericincesurellernice@gmail.com"

MONGODB_URI = os.environ.get(
    'MONGODB_URI',
    'mongodb+srv://Eymen:Eymen6969@cluster0.vqwhlrj.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0'
)

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    logger.info('✅ MongoDB bağlantısı başarılı!')
    
    db = client.chat_db
    messages_collection = db.messages
    rooms_collection = db.rooms
    users_collection = db.users
    friendships_collection = db.friendships
    friend_requests_collection = db.friend_requests
    files_collection = db.files
    banned_users_collection = db.banned_users
    complaints_collection = db.complaints
    
    # Index'leri oluştur
    try:
        messages_collection.create_index([("room", ASCENDING), ("timestamp", DESCENDING)])
        rooms_collection.create_index([("name", ASCENDING)], unique=True)
        users_collection.create_index([("username", ASCENDING)], unique=True)
        users_collection.create_index([("email", ASCENDING)], unique=True)
        users_collection.create_index([("user_id", ASCENDING)], unique=True)
        friendships_collection.create_index([("user_id", ASCENDING), ("friend_id", ASCENDING)], unique=True)
        friend_requests_collection.create_index([("from_id", ASCENDING), ("to_id", ASCENDING)], unique=True)
        files_collection.create_index([("file_id", ASCENDING)], unique=True)
        banned_users_collection.create_index([("user_id", ASCENDING)], unique=True)
        
        # Şikayet koleksiyonu ve index'leri
        complaints_collection.create_index([("complaint_id", ASCENDING)], unique=True)
        complaints_collection.create_index([("created_at", DESCENDING)])
        complaints_collection.create_index([("status", ASCENDING)])
        
        logger.info('✅ Index\'ler oluşturuldu')
        
        # Koleksiyonları test et
        test_collections = {
            'messages': messages_collection,
            'rooms': rooms_collection,
            'users': users_collection,
            'friendships': friendships_collection,
            'friend_requests': friend_requests_collection,
            'files': files_collection,
            'banned_users': banned_users_collection,
            'complaints': complaints_collection
        }
        
        for collection_name, collection in test_collections.items():
            try:
                count = collection.count_documents({})
                logger.info(f'✅ {collection_name} koleksiyonu çalışıyor, {count} doküman')
            except Exception as e:
                logger.error(f'❌ {collection_name} koleksiyonu hatası: {e}')
        
    except Exception as e:
        logger.info(f'ℹ️ Indexler zaten mevcut: {e}')
    
except Exception as e:
    logger.error(f'❌ MongoDB bağlantı hatası: {e}')
    exit(1)

def init_db():
    default_rooms = ['Genel', 'Teknoloji', 'Spor', 'Müzik', 'Oyun']
    for room_name in default_rooms:
        try:
            rooms_collection.insert_one({
                'name': room_name, 
                'created_at': datetime.now(), 
                'type': 'public',
                'created_by': 'system'
            })
            logger.info(f'✅ Oda oluşturuldu: {room_name}')
        except Exception as e:
            logger.info(f'ℹ️ Oda zaten mevcut: {room_name}')

init_db()

def hash_password(password):
    """Şifreyi SHA-256 ile hash'ler."""
    return hashlib.sha256(password.encode()).hexdigest()


def generate_user_id(email):
    """E-posta adresine göre benzersiz ve kalıcı bir ID oluştur"""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:8].upper()

def allowed_file(filename):
    """Dosya uzantısı kontrolü"""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    for file_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return False

def is_user_banned(user_id):
    """Kullanıcının banlı olup olmadığını kontrol et"""
    if not user_id:
        return False
    return banned_users_collection.find_one({'user_id': user_id}) is not None

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grup Sohbet</title>
    <!-- Ses Efekti Elementi -->
    <audio id="notificationSound" src="/static/sounds/notification.mp3" preload="auto"></audio>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.4/socket.io.js"></script>
    <style>
        :root {
            /* Light Mode Colors */
            --bg-primary: #ffffff;
            --bg-secondary: #f8f9fa;
            --bg-tertiary: #e9ecef;
            --text-primary: #212529;
            --text-secondary: #6c757d;
            --text-muted: #adb5bd;
            --border-color: #dee2e6;
            --shadow: rgba(0,0,0,0.1);
            --sidebar-bg: #2c3e50;
            --sidebar-header: #1a252f;
            --sidebar-text: #ecf0f1;
            --sidebar-border: #34495e;
            --message-bg: #ffffff;
            --message-own-bg: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --message-text: #212529;
            --message-own-text: #ffffff;
            --input-bg: #ffffff;
            --input-border: #e0e0e0;
            --input-focus: #667eea;
            --button-primary: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --button-secondary: #e0e0e0;
            --modal-overlay: rgba(0,0,0,0.85);
            --chat-bg: #ecf0f1;
            --card-bg: #ffffff;
        }
        
        [data-theme="dark"] {
            /* Dark Mode Colors */
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-tertiary: #0f3460;
            --text-primary: #e8e8e8;
            --text-secondary: #b8b8b8;
            --text-muted: #888888;
            --border-color: #2c3e50;
            --shadow: rgba(0,0,0,0.3);
            --sidebar-bg: #0f1419;
            --sidebar-header: #000000;
            --sidebar-text: #e8e8e8;
            --sidebar-border: #2c3e50;
            --message-bg: #2c3e50;
            --message-own-bg: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --message-text: #e8e8e8;
            --message-own-text: #ffffff;
            --input-bg: #2c3e50;
            --input-border: #34495e;
            --input-focus: #667eea;
            --button-primary: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --button-secondary: #34495e;
            --modal-overlay: rgba(0,0,0,0.95);
            --chat-bg: #16213e;
            --card-bg: #2c3e50;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            transition: all 0.3s ease;
        }
        
        /* Dark Mode Background */
        [data-theme="dark"] body {
            background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 100%);
        }
        
        /* Loading Screen Styles */
        .loading-screen {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            z-index: 9999;
            transition: opacity 0.5s ease;
        }
        .loading-screen.hidden {
            opacity: 0;
            pointer-events: none;
        }
        .loading-logo {
            font-size: 80px;
            color: white;
            margin-bottom: 20px;
            animation: float 2s ease-in-out infinite;
        }
        .loading-text {
            color: white;
            font-size: 24px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .loading-subtitle {
            color: rgba(255,255,255,0.8);
            font-size: 16px;
            margin-bottom: 30px;
        }
        .loading-spinner {
            width: 50px;
            height: 50px;
            border: 3px solid rgba(255,255,255,0.3);
            border-top: 3px solid white;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .main-container {
            width: 100%;
            max-width: 1400px;
            height: 90vh;
            background: var(--bg-primary);
            border-radius: 20px;
            box-shadow: 0 20px 60px var(--shadow);
            display: none;
            overflow: hidden;
            transition: all 0.3s ease;
        }
        .main-container.active {
            display: flex;
        }
        .sidebar {
            width: 320px;
            background: var(--sidebar-bg);
            display: flex;
            flex-direction: column;
            position: relative;
            transition: all 0.3s ease;
        }
        .sidebar-header {
            padding: 25px 20px;
            background: var(--sidebar-header);
            color: var(--sidebar-text);
            border-bottom: 2px solid var(--sidebar-border);
            transition: all 0.3s ease;
        }
        .sidebar-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }
        .sidebar-header-content {
            flex: 1;
        }
        .sidebar-header h2 {
            font-size: 20px;
            margin-bottom: 8px;
        }
        .user-info {
            font-size: 13px;
            opacity: 0.8;
            color: var(--sidebar-text);
            word-break: break-all;
        }
        .theme-toggle-btn {
            background: var(--button-secondary);
            color: var(--text-primary);
            border: none;
            border-radius: 8px;
            padding: 8px 12px;
            cursor: pointer;
            font-size: 18px;
            transition: all 0.3s ease;
            margin-left: 10px;
        }
        .theme-toggle-btn:hover {
            background: var(--input-focus);
            color: white;
            transform: scale(1.1);
        }
        .user-id-display {
            font-size: 11px;
            color: #95a5a6;
            margin-top: 5px;
            font-family: monospace;
            background: #34495e;
            padding: 5px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .user-id-display:hover {
            background: #667eea;
            color: white;
        }
        .admin-badge {
            background: #e74c3c;
            color: white;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: bold;
            margin-left: 5px;
        }
        .profile-btn, .inbox-btn, .admin-panel-btn, .complaint-btn {
            margin-top: 10px;
            padding: 8px 12px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.2s;
            width: 100%;
        }
        .profile-btn:hover, .inbox-btn:hover, .admin-panel-btn:hover, .complaint-btn:hover {
            background: #764ba2;
        }
        .inbox-btn {
            background: #e74c3c;
        }
        .inbox-btn:hover {
            background: #c0392b;
        }
        .admin-panel-btn {
            background: #f39c12;
        }
        .admin-panel-btn:hover {
            background: #e67e22;
        }
        .complaint-btn {
            background: #e67e22;
        }
        .complaint-btn:hover {
            background: #d35400;
        }
        .sidebar-tabs {
            display: flex;
            background: #1a252f;
            border-bottom: 2px solid #34495e;
        }
        .sidebar-tab {
            flex: 1;
            padding: 12px;
            background: transparent;
            border: none;
            color: #ecf0f1;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.3s;
        }
        .sidebar-tab.active {
            background: #667eea;
            font-weight: bold;
        }
        .rooms-list, .friends-list {
            flex: 1;
            overflow-y: auto;
            padding: 15px 10px;
            display: none;
        }
        .rooms-list.active, .friends-list.active {
            display: block;
        }
        .room-item, .friend-item {
            padding: 15px;
            margin-bottom: 8px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 12px;
            color: #ecf0f1;
            position: relative;
        }
        .room-item:hover, .friend-item:hover {
            background: #34495e;
            transform: translateX(5px);
        }
        .room-item.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            font-weight: 600;
        }
        .room-item.private {
            border-left: 3px solid #f39c12;
        }
        .room-item.group {
            border-left: 3px solid #2ecc71;
        }
        .friend-item.online {
            border-left: 3px solid #2ecc71;
        }
        .friend-item.offline {
            border-left: 3px solid #95a5a6;
            opacity: 0.7;
        }
        .room-icon, .friend-icon {
            font-size: 22px;
        }
        .room-name, .friend-name {
            flex: 1;
            font-size: 15px;
        }
        .friend-status {
            font-size: 10px;
            color: #95a5a6;
        }
        .friend-item.online .friend-status {
            color: #2ecc71;
        }
        .delete-room-btn {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);
            background: #e74c3c;
            color: white;
            border: none;
            border-radius: 4px;
            width: 24px;
            height: 24px;
            cursor: pointer;
            font-size: 12px;
            display: none;
            align-items: center;
            justify-content: center;
        }
        .room-item:hover .delete-room-btn {
            display: flex;
        }
        .delete-room-btn:hover {
            background: #c0392b;
        }
        .new-room-section {
            padding: 15px;
            background: #1a252f;
            border-top: 2px solid #34495e;
        }
        .new-room-input, .private-room-input, .group-user-input, .friend-id-input {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            margin-bottom: 10px;
            font-size: 14px;
            background: #34495e;
            color: white;
        }
        .new-room-input::placeholder, .private-room-input::placeholder, 
        .group-user-input::placeholder, .friend-id-input::placeholder {
            color: #95a5a6;
        }
        .new-room-btn {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: transform 0.2s;
            font-size: 14px;
            margin-bottom: 8px;
        }
        .new-room-btn:hover {
            transform: scale(1.02);
        }
        .private-btn {
            width: 100%;
            padding: 10px;
            background: #f39c12;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 12px;
            transition: transform 0.2s;
            margin-bottom: 8px;
        }
        .private-btn:hover {
            transform: scale(1.02);
        }
        .group-btn {
            width: 100%;
            padding: 10px;
            background: #2ecc71;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 12px;
            transition: transform 0.2s;
            margin-bottom: 8px;
        }
        .group-btn:hover {
            transform: scale(1.02);
        }
        .friend-btn {
            width: 100%;
            padding: 10px;
            background: #9b59b6;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 12px;
            transition: transform 0.2s;
        }
        .friend-btn:hover {
            transform: scale(1.02);
        }
        
        /* Sağ panel stilleri */
        .right-panel {
            width: 300px;
            background: #34495e;
            border-left: 2px solid #2c3e50;
            display: none;
            flex-direction: column;
            transition: all 0.3s ease;
        }
        .right-panel.active {
            display: flex;
        }
        .right-panel-header {
            padding: 20px;
            background: #2c3e50;
            color: white;
            border-bottom: 2px solid #2c3e50;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .right-panel-header h3 {
            font-size: 16px;
            color: #ecf0f1;
        }
        .close-panel-btn {
            background: none;
            border: none;
            color: #ecf0f1;
            font-size: 18px;
            cursor: pointer;
            padding: 5px;
            border-radius: 4px;
            transition: background 0.2s;
        }
        .close-panel-btn:hover {
            background: rgba(255,255,255,0.1);
        }
        .right-panel-content {
            flex: 1;
            overflow-y: auto;
            padding: 15px;
        }
        .panel-room-item, .panel-friend-item {
            padding: 12px 15px;
            margin-bottom: 8px;
            background: #2c3e50;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            color: #ecf0f1;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .panel-room-item:hover, .panel-friend-item:hover {
            background: #3c5a7a;
            transform: translateX(3px);
        }
        .panel-room-item.active, .panel-friend-item.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .panel-room-icon, .panel-friend-icon {
            font-size: 18px;
        }
        .panel-room-name, .panel-friend-name {
            flex: 1;
            font-size: 14px;
        }
        .panel-friend-status {
            font-size: 10px;
            color: #95a5a6;
        }
        .panel-friend-item.online .panel-friend-status {
            color: #2ecc71;
        }
        
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--chat-bg);
            transition: all 0.3s ease;
        }
        .chat-header {
            background: var(--button-primary);
            color: white;
            padding: 20px 25px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: all 0.3s ease;
        }
        .chat-header h2 {
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .logout-btn {
            padding: 10px 20px;
            background: rgba(255,255,255,0.2);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .logout-btn:hover {
            background: rgba(255,255,255,0.3);
        }
        .messages {
            flex: 1;
            padding: 25px;
            overflow-y: auto;
            background: var(--chat-bg);
            transition: all 0.3s ease;
        }
        .message {
            margin-bottom: 20px;
            animation: slideIn 0.3s ease;
            display: flex;
            gap: 12px;
        }
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message-avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 16px;
            flex-shrink: 0;
            overflow: hidden;
        }
        .message-avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .message-content-wrapper {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        .message-content {
            background: var(--message-bg);
            padding: 14px 18px;
            border-radius: 18px;
            box-shadow: 0 2px 8px var(--shadow);
            max-width: 65%;
            word-wrap: break-word;
            transition: all 0.3s ease;
        }
        .message.own {
            flex-direction: row-reverse;
        }
        .message.own .message-content {
            background: var(--message-own-bg);
            color: var(--message-own-text);
        }
        .username {
            font-weight: 700;
            font-size: 14px;
            margin-bottom: 6px;
            color: var(--input-focus);
            transition: all 0.3s ease;
        }
        .message.own .username {
            color: var(--message-own-text);
        }
        .message-text {
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 6px;
        }
        .timestamp {
            font-size: 11px;
            color: #7f8c8d;
            font-weight: 500;
        }
        .message.own .timestamp {
            color: rgba(255,255,255,0.8);
        }
        .file-message {
            margin-top: 8px;
            padding: 10px;
            background: rgba(0,0,0,0.05);
            border-radius: 8px;
            border: 1px solid rgba(0,0,0,0.1);
        }
        .file-message img, .file-message video {
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
        }
        .audio-message {
            margin-top: 8px;
            padding: 10px;
            background: rgba(0,0,0,0.05);
            border-radius: 8px;
            border: 1px solid rgba(0,0,0,0.1);
        }
        .audio-message audio {
            width: 100%;
            max-width: 300px;
        }
        .file-info {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 5px;
        }
        .file-icon {
            font-size: 20px;
        }
        .file-download {
            padding: 4px 8px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 12px;
            margin-left: auto;
        }
        .file-download:hover {
            background: #764ba2;
        }
        .input-area {
            padding: 20px 25px;
            background: white;
            border-top: 2px solid #e0e0e0;
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }
        .file-upload-area {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 10px;
        }
        .file-input-wrapper {
            position: relative;
            display: inline-block;
        }
        .file-input {
            position: absolute;
            left: -9999px;
        }
        .file-input-label {
            padding: 10px 15px;
            background: #f39c12;
            color: white;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.2s;
        }
        .file-input-label:hover {
            background: #e67e22;
        }
        .voice-record-btn {
            padding: 10px 15px;
            background: #e74c3c;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.2s;
        }
        .voice-record-btn:hover {
            background: #c0392b;
        }
        .voice-record-btn.recording {
            background: #c0392c;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.05); }
            100% { transform: scale(1); }
        }
        .recording-timer {
            font-size: 12px;
            color: #e74c3c;
            font-weight: bold;
            margin-left: 10px;
        }
        .selected-files {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-top: 5px;
        }
        .file-tag {
            background: #e0e0e0;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .remove-file {
            cursor: pointer;
            color: #e74c3c;
            font-weight: bold;
        }
        .message-input-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        input.message-input {
            flex: 1;
            padding: 14px 20px;
            border: 2px solid #e0e0e0;
            border-radius: 25px;
            font-size: 15px;
            outline: none;
            transition: border 0.3s;
        }
        input.message-input:focus { 
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
        }
        button.send-btn {
            padding: 14px 35px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 25px;
            cursor: pointer;
            font-weight: bold;
            transition: transform 0.2s;
            font-size: 15px;
            white-space: nowrap;
        }
        button.send-btn:hover { 
            transform: scale(1.05);
            box-shadow: 0 5px 15px rgba(102,126,234,0.4);
        }
        button.send-btn:active { transform: scale(0.95); }
        
        .auth-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.85);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .auth-modal.hidden {
            display: none;
        }
        .auth-box {
            background: white;
            padding: 45px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            min-width: 400px;
            max-width: 450px;
        }
        .auth-header {
            text-align: center;
            margin-bottom: 30px;
        }
        .auth-header h2 {
            color: #667eea;
            font-size: 28px;
            margin-bottom: 10px;
        }
        .auth-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 30px;
            background: #f0f0f0;
            padding: 5px;
            border-radius: 10px;
        }
        .auth-tab {
            flex: 1;
            padding: 12px;
            background: transparent;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s;
            color: #666;
        }
        .auth-tab.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .auth-form {
            display: none;
        }
        .auth-form.active {
            display: block;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 600;
            font-size: 14px;
        }
        .form-input {
            width: 100%;
            padding: 14px 18px;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            font-size: 15px;
            outline: none;
            transition: border 0.3s;
        }
        .form-input:focus {
            border-color: #667eea;
        }
        .auth-btn {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: bold;
            font-size: 16px;
            transition: transform 0.2s;
        }
        .auth-btn:hover {
            transform: scale(1.02);
        }
        .error-message {
            background: #fee;
            color: #c33;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 15px;
            font-size: 14px;
            display: none;
        }
        .success-message {
            background: #efe;
            color: #3c3;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 15px;
            font-size: 14px;
            display: none;
        }
        
        .profile-modal, .inbox-modal, .admin-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.85);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .profile-modal.active, .inbox-modal.active, .admin-modal.active {
            display: flex;
        }
        .profile-box, .inbox-box {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            min-width: 450px;
            max-width: 800px;
            max-height: 80vh;
            overflow-y: auto;
        }
        .admin-box {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            min-width: 650px;
            max-width: 900px;
            max-height: 80vh;
            overflow-y: auto;
        }
        .profile-header, .inbox-header, .admin-header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid #e0e0e0;
        }
        .profile-avatar {
            width: 100px;
            height: 100px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
            color: white;
            margin: 0 auto 15px;
            cursor: pointer;
            position: relative;
            overflow: hidden;
        }
        .profile-avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: 50%;
        }
        .profile-avatar-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.3s;
            border-radius: 50%;
        }
        .profile-avatar:hover .profile-avatar-overlay {
            opacity: 1;
        }
        .profile-camera-icon {
            color: white;
            font-size: 24px;
        }
        .profile-info {
            margin-bottom: 30px;
        }
        .profile-field {
            margin-bottom: 20px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 10px;
        }
        .profile-field label {
            display: block;
            font-size: 12px;
            color: #666;
            margin-bottom: 5px;
            font-weight: 600;
        }
        .profile-field-value {
            font-size: 16px;
            color: #333;
            font-weight: 500;
        }
        .profile-actions {
            display: flex;
            gap: 10px;
        }
        .profile-close-btn {
            flex: 1;
            padding: 12px;
            background: #e0e0e0;
            color: #333;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .profile-close-btn:hover {
            background: #d0d0d0;
        }
        
        .inbox-item {
            padding: 15px;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .inbox-user {
            font-weight: bold;
            color: #333;
        }
        .inbox-actions {
            display: flex;
            gap: 5px;
        }
        .inbox-accept-btn {
            padding: 8px 15px;
            background: #2ecc71;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        .inbox-reject-btn {
            padding: 8px 15px;
            background: #e74c3c;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        .empty-inbox {
            text-align: center;
            padding: 40px;
            color: #7f8c8d;
        }
        
        .admin-users-list {
            max-height: 500px;
            overflow-y: auto;
            margin-bottom: 20px;
            padding-right: 5px;
        }
        .admin-users-list::-webkit-scrollbar {
            width: 8px;
        }
        .admin-users-list::-webkit-scrollbar-track {
            background: #f1f1f1;
            border-radius: 10px;
        }
        .admin-users-list::-webkit-scrollbar-thumb {
            background: #667eea;
            border-radius: 10px;
        }
        .admin-users-list::-webkit-scrollbar-thumb:hover {
            background: #764ba2;
        }
        
        .user-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 14px;
        }

        .user-table thead th {
            background-color: #f39c12;
            color: white;
            padding: 12px 15px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .user-table tbody tr {
            border-bottom: 1px solid #eee;
            transition: background-color 0.3s;
        }

        .user-table tbody tr:nth-child(even) {
            background-color: #f8f9fa;
        }

        .user-table tbody tr:hover {
            background-color: #e8eaf6;
        }

        .user-table td {
            padding: 12px 15px;
            vertical-align: middle;
        }

        .user-table td:last-child {
            text-align: center;
        }
        
        .ban-btn, .unban-btn {
            padding: 6px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            border: none;
            white-space: nowrap;
            display: block;
            margin: 2px auto;
        }

        .ban-btn {
            background: #e74c3c;
            color: white;
        }

        .unban-btn {
            background: #2ecc71;
            color: white;
        }
        
        .user-table td:nth-child(1) { font-weight: bold; }
        .user-table td:nth-child(3) { color: #3498db; }
        .user-table td:nth-child(4) { font-size: 11px; color: #95a5a6; }
        .user-table td:nth-child(5) { font-weight: bold; }
        .user-table td:nth-child(6) { width: 120px; }
        .ban-btn {
            padding: 8px 15px;
            background: #e74c3c;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        .ban-btn:hover {
            background: #c0392b;
        }
        .unban-btn {
            padding: 8px 15px;
            background: #2ecc71;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        .unban-btn:hover {
            background: #27ae60;
        }
        
        .group-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.85);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .group-modal.active {
            display: flex;
        }
        .group-box {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            min-width: 450px;
            max-width: 500px;
        }
        .group-header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid #e0e0e0;
        }
        .group-header h2 {
            color: #2ecc71;
            font-size: 24px;
            margin-bottom: 10px;
        }
        
        /* Profil fotoğrafı ve şifre değiştirme stilleri */
        .profile-picture-input {
            display: none;
        }
        .password-section {
            margin-top: 30px;
            padding-top: 30px;
            border-top: 2px solid #e0e0e0;
        }
        .password-section h3 {
            color: #667eea;
            margin-bottom: 20px;
            font-size: 18px;
        }
        .profile-btn-primary {
            flex: 2;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .profile-btn-primary:hover {
            transform: scale(1.02);
        }
        
        /* Şikayet Modal Stilleri */
        .complaint-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--modal-overlay);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .complaint-modal.active {
            display: flex;
        }
        .complaint-box {
            background: var(--card-bg);
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px var(--shadow);
            min-width: 450px;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
            transition: all 0.3s ease;
        }
        .complaint-header {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid var(--border-color);
        }
        .complaint-header h2 {
            color: #e67e22;
            font-size: 24px;
            margin-bottom: 10px;
        }
        
        /* Admin Panel Sekmeleri */
        .admin-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .admin-tab {
            flex: 1;
            padding: 12px;
            background: var(--button-secondary);
            color: var(--text-primary);
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s;
        }
        .admin-tab.active {
            background: var(--button-primary);
            color: white;
        }
        .admin-content {
            flex: 1;
            overflow: hidden;
        }
        
        /* Şikayet Listesi Stilleri */
        .complaint-item {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            transition: all 0.3s ease;
        }
        .complaint-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px var(--shadow);
        }
        .complaint-header-info {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .complaint-target {
            font-weight: bold;
            color: #e67e22;
            font-size: 16px;
        }
        .complaint-status {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .complaint-status.pending {
            background: #f39c12;
            color: white;
        }
        .complaint-status.resolved {
            background: #27ae60;
            color: white;
        }
        .complaint-status.dismissed {
            background: #e74c3c;
            color: white;
        }
        .complaint-content {
            margin-bottom: 15px;
        }
        .complaint-reason {
            color: var(--text-primary);
            line-height: 1.6;
            margin-bottom: 10px;
        }
        .complaint-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            color: var(--text-secondary);
        }
        .complaint-actions {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        .complaint-action-btn {
            flex: 1;
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        .resolve-btn {
            background: #27ae60;
            color: white;
        }
        .resolve-btn:hover {
            background: #229954;
        }
        .dismiss-btn {
            background: #e74c3c;
            color: white;
        }
        .dismiss-btn:hover {
            background: #c0392b;
        }
        
        .empty-state {
            text-align: center;
            padding: 40px;
            color: var(--text-secondary);
            transition: all 0.3s ease;
        }
        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 15px;
        }
        @media (max-width: 768px) {
            .sidebar { width: 250px; }
            .right-panel { width: 250px; }
            .main-container { height: 95vh; }
            .auth-box, .profile-box, .group-box, .inbox-box, .admin-box, .complaint-box { min-width: 90%; }
            .input-area { flex-direction: column; }
            .file-upload-area { order: -1; }
        }
    </style>
</head>
<body>
    <!-- Loading Screen -->
    <div class="loading-screen" id="loadingScreen">
        <div class="loading-logo">💬</div>
        <div class="loading-text">Grup Sohbet</div>
        <div class="loading-subtitle">Yükleniyor...</div>
        <div class="loading-spinner"></div>
    </div>
    
    <div class="auth-modal" id="authModal">
        <div class="auth-box">
            <div class="auth-header">
                <h2>💬 Grup Sohbet</h2>
                <p style="color: #666; font-size: 14px;">Hesabınızla giriş yapın veya yeni hesap oluşturun</p>
            </div>
            
            <div class="auth-tabs">
                <button class="auth-tab active" id="loginTab">Giriş Yap</button>
                <button class="auth-tab" id="registerTab">Kayıt Ol</button>
            </div>
            
            <div id="errorMessage" class="error-message"></div>
            <div id="successMessage" class="success-message"></div>
            
            <div id="loginForm" class="auth-form active">
                <div class="form-group">
                    <label>Kullanıcı Adı</label>
                    <input type="text" class="form-input" id="loginUsername" placeholder="Kullanıcı adınız">
                </div>
                <div class="form-group">
                    <label>Şifre</label>
                    <input type="password" class="form-input" id="loginPassword" placeholder="Şifreniz">
                </div>
                <button class="auth-btn" id="loginBtn">Giriş Yap</button>
            </div>
            
            <div id="registerForm" class="auth-form">
                <div class="form-group">
                    <label>Kullanıcı Adı</label>
                    <input type="text" class="form-input" id="registerUsername" placeholder="Kullanıcı adınız" maxlength="20">
                </div>
                <div class="form-group">
                    <label>E-posta</label>
                    <input type="email" class="form-input" id="registerEmail" placeholder="E-posta adresiniz">
                </div>
                <div class="form-group">
                    <label>Şifre</label>
                    <input type="password" class="form-input" id="registerPassword" placeholder="Şifreniz (min. 6 karakter)">
                </div>
                <div class="form-group">
                    <label>Şifre Tekrar</label>
                    <input type="password" class="form-input" id="registerPasswordConfirm" placeholder="Şifrenizi tekrar girin">
                </div>
                <button class="auth-btn" id="registerBtn">Kayıt Ol</button>
            </div>
        </div>
    </div>
    
    <div class="profile-modal" id="profileModal">
        <div class="profile-box">
            <div class="profile-header">
                <div class="profile-avatar" id="profileAvatar" onclick="document.getElementById('profilePictureInput').click()">
                    <span id="profileAvatarText">👤</span>
                    <img id="profileAvatarImage" style="display: none;" alt="Profil Fotoğrafı">
                    <div class="profile-avatar-overlay">
                        <span class="profile-camera-icon">📷</span>
                    </div>
                </div>
                <h2 id="profileUsername" style="color: #667eea; margin-bottom: 5px;"></h2>
                <p style="color: #999; font-size: 13px;" id="profileJoinDate"></p>
                <input type="file" id="profilePictureInput" class="profile-picture-input" accept="image/*">
            </div>
            <div class="profile-info">
                <div class="profile-field">
                    <label>E-POSTA</label>
                    <div class="profile-field-value" id="profileEmail"></div>
                </div>
                <div class="profile-field">
                    <label>KULLANICI ID</label>
                    <div class="profile-field-value" id="profileUserId" style="font-family: monospace;"></div>
                </div>
                <div class="profile-field">
                    <label>YETKİ</label>
                    <div class="profile-field-value" id="profileRole"></div>
                </div>
            </div>
            
            <div class="password-section">
                <h3>🔒 Şifre Değiştir</h3>
                <div class="form-group">
                    <label>Mevcut Şifre</label>
                    <input type="password" class="form-input" id="currentPassword" placeholder="Mevcut şifreniz">
                </div>
                <div class="form-group">
                    <label>Yeni Şifre</label>
                    <input type="password" class="form-input" id="newPassword" placeholder="Yeni şifreniz (min. 6 karakter)">
                </div>
                <div class="form-group">
                    <label>Yeni Şifre Tekrar</label>
                    <input type="password" class="form-input" id="confirmPassword" placeholder="Yeni şifrenizi tekrar girin">
                </div>
                <button class="auth-btn" id="changePasswordBtn">🔒 Şifre Değiştir</button>
            </div>
            
            <div class="profile-actions" style="margin-top: 30px;">
                <button class="profile-close-btn" id="closeProfileBtn">Kapat</button>
            </div>
        </div>
    </div>
    
    <div class="inbox-modal" id="inboxModal">
        <div class="inbox-box">
            <div class="inbox-header">
                <h2 style="color: #e74c3c;">📬 Gelen Kutusu</h2>
                <p style="color: #666; font-size: 14px;">Arkadaşlık istekleriniz</p>
            </div>
            <div id="inboxList">
                <div class="empty-inbox">
                    <div style="font-size: 48px; margin-bottom: 15px;">📭</div>
                    <p>Henüz arkadaşlık isteğiniz yok</p>
                </div>
            </div>
            <div class="profile-actions" style="margin-top: 20px;">
                <button class="profile-close-btn" id="closeInboxBtn">Kapat</button>
            </div>
        </div>
    </div>
    
    <div class="complaint-modal" id="complaintModal">
        <div class="complaint-box">
            <div class="complaint-header">
                <h2 style="color: #e67e22;">🚨 Şikayet Et</h2>
                <p style="color: #666; font-size: 14px;">Bir kullanıcıdan şikayet etmek için bu formu doldurun</p>
            </div>
            
            <div id="complaintErrorMessage" class="error-message"></div>
            <div id="complaintSuccessMessage" class="success-message"></div>
            
            <div class="form-group">
                <label>Şikayet Edilen Kullanıcı Adı</label>
                <input type="text" class="form-input" id="complaintTargetUsername" placeholder="Şikayet edilen kullanıcının adı">
            </div>
            <div class="form-group">
                <label>Şikayet Nedeni</label>
                <textarea class="form-input" id="complaintReason" placeholder="Şikayetinizi detaylı bir şekilde açıklayın..." rows="4" style="resize: vertical;"></textarea>
            </div>
            
            <div class="profile-actions">
                <button class="profile-close-btn" id="closeComplaintBtn">İptal</button>
                <button class="profile-btn-primary" id="submitComplaintBtn">🚨 Şikayet Gönder</button>
            </div>
        </div>
    </div>
    
    <div class="admin-modal" id="adminModal">
        <div class="admin-box">
            <div class="admin-header">
                <div class="admin-tabs">
                    <button class="admin-tab active" id="usersTab">👥 Kullanıcılar</button>
                    <button class="admin-tab" id="complaintsTab">🚨 Şikayetler</button>
                </div>
                <p id="adminUserCount" style="color: #667eea; font-size: 13px; font-weight: bold; margin-top: 8px;">Toplam Kullanıcı: 0</p>
            </div>
            <div class="admin-content">
                <div class="admin-users-list" id="adminUsersList">
                    <table id="adminUsersTable" class="user-table">
                        <thead>
                            <tr>
                                <th>Kullanıcı Adı</th>
                                <th>ID</th>
                                <th>E-posta</th>
                                <th>Kayıt Tarihi</th>
                                <th>Durum</th>
                                <th>İşlemler</th>
                            </tr>
                        </thead>
                        <tbody id="adminUsersTableBody">
                            <!-- Kullanıcılar buraya JavaScript ile eklenecek -->
                        </tbody>
                    </table>
                    <div id="emptyState" class="empty-state" style="display: none;">
                        <div style="font-size: 48px; margin-bottom: 15px;">👥</div>
                        <p>Kullanıcı bulunamadı</p>
                    </div>
                </div>
                
                <div class="admin-complaints-list" id="adminComplaintsList" style="display: none;">
                    <div id="complaintsEmptyState" class="empty-state" style="display: none;">
                        <div style="font-size: 48px; margin-bottom: 15px;">🚨</div>
                        <p>Şikayet bulunamadı</p>
                    </div>
                    <div id="complaintsList">
                        <!-- Şikayetler buraya JavaScript ile eklenecek -->
                    </div>
                </div>
            </div>
            <div class="profile-actions">
                <button class="profile-close-btn" id="closeAdminBtn">Kapat</button>
            </div>
        </div>
    </div>
    
    <div class="group-modal" id="groupModal">
        <div class="group-box">
            <div class="group-header">
                <h2>👥 Grup Oluştur</h2>
                <p style="color: #666; font-size: 14px;">En fazla 3 kişilik grup oluşturabilirsiniz</p>
            </div>
            
            <div id="groupErrorMessage" class="error-message"></div>
            <div id="groupSuccessMessage" class="success-message"></div>
            
            <div class="form-group">
                <label>Grup Adı</label>
                <input type="text" class="form-input" id="groupNameInput" placeholder="Grup adını girin" maxlength="30">
            </div>
            <div class="form-group">
                <label>1. Kullanıcı ID</label>
                <input type="text" class="form-input group-user-input" id="groupUser1Input" placeholder="İlk kullanıcı ID'si">
            </div>
            <div class="form-group">
                <label>2. Kullanıcı ID</label>
                <input type="text" class="form-input group-user-input" id="groupUser2Input" placeholder="İkinci kullanıcı ID'si">
            </div>
            
            <div class="profile-actions">
                <button class="profile-close-btn" id="closeGroupBtn">İptal</button>
                <button class="auth-btn" id="createGroupBtn" style="flex: 2;">Grup Oluştur</button>
            </div>
        </div>
    </div>
    
    <div class="main-container" id="mainContainer">
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-header-content">
                    <h2>🏠 Sohbet</h2>
                    <div class="user-info" id="userInfo"></div>
                    <div class="user-id-display" id="userIdDisplay" title="Kliklayarak kopyala"></div>
                    <button class="profile-btn" id="profileBtn">👤 Profilim</button>
                    <button class="inbox-btn" id="inboxBtn">📬 Gelen Kutusu <span id="inboxBadge" style="display: none;">0</span></button>
                    <button class="complaint-btn" id="complaintBtn">🚨 Şikayet Et</button>
                    <button class="admin-panel-btn" id="adminPanelBtn" style="display: none;">👑 Admin Panel</button>
                </div>
                <button class="theme-toggle-btn" id="themeToggleBtn" title="Temayı Değiştir">🌙</button>
            </div>
            
            <div class="sidebar-tabs">
                <button class="sidebar-tab active" id="roomsTab">Odalar</button>
                <button class="sidebar-tab" id="friendsTab">Arkadaşlar</button>
            </div>
            
            <div class="rooms-list active" id="roomsList"></div>
            <div class="friends-list" id="friendsList"></div>
            
            <div class="new-room-section">
                <input type="text" class="new-room-input" id="newRoomInput" placeholder="Yeni oda adı" maxlength="30">
                <button class="new-room-btn" id="createRoomBtn">➕ Oda Oluştur</button>
                
                <input type="text" class="private-room-input" id="privateUserIdInput" placeholder="Özel sohbet için ID girin" maxlength="50">
                <button class="private-btn" id="privateChatBtn">🔒 Özel Sohbet</button>
                
                <input type="text" class="friend-id-input" id="friendIdInput" placeholder="Arkadaş eklemek için ID girin" maxlength="50">
                <button class="friend-btn" id="friendBtn">👥 Arkadaş Ekle</button>
                
                <button class="group-btn" id="groupBtn">👥 Grup Oluştur</button>
            </div>
        </div>
        
        <div class="chat-container">
            <div class="chat-header">
                <h2 id="currentRoomName"><span class="room-icon">💬</span> Genel</h2>
                <button class="logout-btn" id="logoutBtn">Çıkış Yap</button>
            </div>
            <div class="messages" id="messages">
                <div class="empty-state">
                    <div class="empty-state-icon">💬</div>
                    <p>Henüz mesaj yok. İlk mesajı sen gönder!</p>
                </div>
            </div>
            <div class="input-area">
                <div class="file-upload-area">
                    <div class="file-input-wrapper">
                        <input type="file" class="file-input" id="fileInput" multiple accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.txt,.zip,.rar">
                        <label for="fileInput" class="file-input-label">📎 Dosya Ekle</label>
                    </div>
                    <button class="voice-record-btn" id="voiceRecordBtn">🎤 Ses Kaydet</button>
                    <div id="recordingTimer" class="recording-timer" style="display: none;"></div>
                    <div class="selected-files" id="selectedFiles"></div>
                </div>
                <div class="message-input-container">
                    <input type="text" class="message-input" id="messageInput" placeholder="Mesajınızı yazın..." maxlength="500">
                </div>
                <button class="send-btn" id="sendBtn">Gönder</button>
            </div>
        </div>
        
        <!-- Sağ Panel -->
        <div class="right-panel" id="rightPanel">
            <div class="right-panel-header">
                <h3 id="rightPanelTitle">Odalar</h3>
                <button class="close-panel-btn" id="closePanelBtn">×</button>
            </div>
            <div class="right-panel-content" id="rightPanelContent">
                <!-- İçerik buraya dinamik olarak yüklenecek -->
            </div>
        </div>
    </div>
    
    <script>
        let socket;
        let username = '';
        let userId = '';
        let userEmail = '';
        let currentRoom = 'Genel';
        let isAdmin = false;
        let selectedFiles = [];
        let currentPanelType = ''; // 'rooms' veya 'friends'
        
        // Ses kaydı değişkenleri
        let mediaRecorder = null;
        let audioChunks = [];
        let isRecording = false;
        let recordingTimer = null;
        let recordingStartTime = 0;
        
        // Event Listeners
        document.addEventListener('DOMContentLoaded', function() {
            // Tema ayarlarını yükle
            loadThemeSettings();
            
            // Loading screen'i gizle ve ana sayfayı göster
            setTimeout(() => {
                const loadingScreen = document.getElementById('loadingScreen');
                if (loadingScreen) {
                    loadingScreen.classList.add('hidden');
                    setTimeout(() => {
                        loadingScreen.style.display = 'none';
                    }, 500);
                }
            }, 2000);
            
            // Auth tabs
            document.getElementById('loginTab').addEventListener('click', () => switchTab('login'));
            document.getElementById('registerTab').addEventListener('click', () => switchTab('register'));
            
            // Auth buttons
            document.getElementById('loginBtn').addEventListener('click', login);
            document.getElementById('registerBtn').addEventListener('click', register);
            
            // Profile buttons
            document.getElementById('profileBtn').addEventListener('click', showProfile);
            document.getElementById('closeProfileBtn').addEventListener('click', closeProfile);
            
            // Profil fotoğrafı input
            document.getElementById('profilePictureInput').addEventListener('change', handleProfilePictureUpload);
            
            // Şifre değiştirme butonu
            document.getElementById('changePasswordBtn').addEventListener('click', changePassword);
            
            // Tema değiştirme butonu
            document.getElementById('themeToggleBtn').addEventListener('click', toggleTheme);
            
            // Şikayet butonları
            document.getElementById('complaintBtn').addEventListener('click', showComplaintModal);
            document.getElementById('closeComplaintBtn').addEventListener('click', closeComplaintModal);
            document.getElementById('submitComplaintBtn').addEventListener('click', submitComplaint);
            
            // Admin panel sekmeleri
            document.getElementById('usersTab').addEventListener('click', () => switchAdminTab('users'));
            document.getElementById('complaintsTab').addEventListener('click', () => switchAdminTab('complaints'));
            
            // Inbox buttons
            document.getElementById('inboxBtn').addEventListener('click', showInbox);
            document.getElementById('closeInboxBtn').addEventListener('click', closeInbox);
            
            // Admin buttons
            document.getElementById('adminPanelBtn').addEventListener('click', showAdminPanel);
            document.getElementById('closeAdminBtn').addEventListener('click', closeAdminPanel);
            
            // Group buttons
            document.getElementById('groupBtn').addEventListener('click', showGroupModal);
            document.getElementById('closeGroupBtn').addEventListener('click', closeGroupModal);
            document.getElementById('createGroupBtn').addEventListener('click', createGroup);
            
            // Chat buttons
            document.getElementById('createRoomBtn').addEventListener('click', createRoom);
            document.getElementById('privateChatBtn').addEventListener('click', startPrivateChat);
            document.getElementById('friendBtn').addEventListener('click', sendFriendRequest);
            document.getElementById('sendBtn').addEventListener('click', sendMessage);
            document.getElementById('logoutBtn').addEventListener('click', logout);
            
            // File input
            document.getElementById('fileInput').addEventListener('change', handleFileSelect);
            
            // Ses kaydı butonu
            document.getElementById('voiceRecordBtn').addEventListener('click', toggleVoiceRecording);
            
            // Sidebar tabs
            document.getElementById('roomsTab').addEventListener('click', () => switchSidebarTab('rooms'));
            document.getElementById('friendsTab').addEventListener('click', () => switchSidebarTab('friends'));
            
            // Right panel
            document.getElementById('closePanelBtn').addEventListener('click', closeRightPanel);
            
            // Enter key events
            document.getElementById('messageInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') sendMessage();
            });
            
            document.getElementById('newRoomInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') createRoom();
            });
            
            document.getElementById('privateUserIdInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') startPrivateChat();
            });
            
            document.getElementById('friendIdInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') sendFriendRequest();
            });
            
            document.getElementById('loginUsername').addEventListener('keypress', e => {
                if (e.key === 'Enter') login();
            });
            
            document.getElementById('loginPassword').addEventListener('keypress', e => {
                if (e.key === 'Enter') login();
            });
            
            document.getElementById('registerPasswordConfirm').addEventListener('keypress', e => {
                if (e.key === 'Enter') register();
            });
            
            document.getElementById('groupNameInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') createGroup();
            });
            
            // Copy user ID
            document.getElementById('userIdDisplay').addEventListener('click', copyUserId);
        });
        
        // Profil fotoğrafı yükleme fonksiyonu
        function handleProfilePictureUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            // Sadece resim dosyalarına izin ver
            if (!file.type.startsWith('image/')) {
                showError('Sadece resim dosyaları yüklenebilir!');
                return;
            }
            
            // Dosya boyutu kontrolü (5MB)
            if (file.size > 5 * 1024 * 1024) {
                showError('Profil fotoğrafı maksimum 5MB olabilir!');
                return;
            }
            
            const formData = new FormData();
            formData.append('profile_picture', file);
            
            fetch('/api/upload_profile_picture', {
                method: 'POST',
                body: formData
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showSuccess('✅ Profil fotoğrafı başarıyla güncellendi!');
                    // Profil fotoğrafını güncelle
                    loadProfilePicture();
                } else {
                    showError(data.message || 'Profil fotoğrafı güncellenemedi!');
                }
            })
            .catch(() => showError('Profil fotoğrafı yüklenirken hata oluştu!'));
            
            // Input'u temizle
            event.target.value = '';
        }
        
        // Profil fotoğrafını yükleme fonksiyonu
        function loadProfilePicture() {
            fetch('/api/profile')
            .then(res => res.json())
            .then(data => {
                if (data.success && data.profile_picture) {
                    const profileAvatarImage = document.getElementById('profileAvatarImage');
                    const profileAvatarText = document.getElementById('profileAvatarText');
                    
                    profileAvatarImage.src = `/api/files/${data.profile_picture}`;
                    profileAvatarImage.style.display = 'block';
                    profileAvatarText.style.display = 'none';
                }
            });
        }
        
        // Şifre değiştirme fonksiyonu
       function changePassword() {
    const currentPassword = document.getElementById('currentPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;

    if (!currentPassword || !newPassword || !confirmPassword) {
        showError('Tüm alanları doldurun!');
        return;
    }

    if (newPassword.length < 6) {
        showError('Yeni şifre en az 6 karakter olmalı!');
        return;
    }

    if (newPassword !== confirmPassword) {
        showError('Yeni şifreler eşleşmiyor!');
        return;
    }

    fetch('/api/change_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            confirm_password: confirmPassword
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showSuccess('✅ Şifre başarıyla değiştirildi!');
            // Formu temizle
            document.getElementById('currentPassword').value = '';
            document.getElementById('newPassword').value = '';
            document.getElementById('confirmPassword').value = '';
        } else {
            showError(data.message || 'Şifre değiştirilemedi!');
        }
    })
    .catch(() => showError('Şifre değiştirilirken hata oluştu!'));
}

        
        // Ses kaydı fonksiyonları
        async function toggleVoiceRecording() {
            if (!isRecording) {
                await startRecording();
            } else {
                stopRecording();
            }
        }
        
        async function startRecording() {
            try {
                // Mikrofon erişimi için izin iste
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                
                // MediaRecorder'ı başlat
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                
                mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0) {
                        audioChunks.push(event.data);
                    }
                };
                
                mediaRecorder.onstop = () => {
                    // Ses kaydını tamamla ve dosya oluştur
                    const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                    const audioFile = new File([audioBlob], `ses-kaydi-${Date.now()}.wav`, { type: 'audio/wav' });
                    
                    // Seçili dosyalara ekle
                    selectedFiles.push(audioFile);
                    updateSelectedFilesDisplay();
                    
                    // Stream'i durdur
                    stream.getTracks().forEach(track => track.stop());
                };
                
                // Kaydı başlat
                mediaRecorder.start();
                isRecording = true;
                
                // UI güncelle
                document.getElementById('voiceRecordBtn').classList.add('recording');
                document.getElementById('voiceRecordBtn').innerHTML = '⏹️ Kaydı Durdur';
                document.getElementById('recordingTimer').style.display = 'block';
                
                // Zamanlayıcıyı başlat
                recordingStartTime = Date.now();
                updateRecordingTimer();
                
            } catch (error) {
                console.error('Mikrofon erişimi hatası:', error);
                showError('Mikrofon erişimi reddedildi veya kullanılamıyor!');
            }
        }
        
        function stopRecording() {
            if (mediaRecorder && isRecording) {
                mediaRecorder.stop();
                isRecording = false;
                
                // UI güncelle
                document.getElementById('voiceRecordBtn').classList.remove('recording');
                document.getElementById('voiceRecordBtn').innerHTML = '🎤 Ses Kaydet';
                document.getElementById('recordingTimer').style.display = 'none';
                
                // Zamanlayıcıyı durdur
                if (recordingTimer) {
                    clearInterval(recordingTimer);
                    recordingTimer = null;
                }
            }
        }
        
        function updateRecordingTimer() {
            recordingTimer = setInterval(() => {
                const elapsedTime = Math.floor((Date.now() - recordingStartTime) / 1000);
                const minutes = Math.floor(elapsedTime / 60);
                const seconds = elapsedTime % 60;
                document.getElementById('recordingTimer').textContent = 
                    `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }, 1000);
        }
        
        function switchTab(tab) {
            document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
            
            if (tab === 'login') {
                document.getElementById('loginTab').classList.add('active');
                document.getElementById('loginForm').classList.add('active');
            } else {
                document.getElementById('registerTab').classList.add('active');
                document.getElementById('registerForm').classList.add('active');
            }
            hideMessages();
        }
        
        function switchSidebarTab(tab) {
            document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.rooms-list, .friends-list').forEach(f => f.classList.remove('active'));
            
            if (tab === 'rooms') {
                document.getElementById('roomsTab').classList.add('active');
                document.getElementById('roomsList').classList.add('active');
                showRightPanel('rooms');
            } else {
                document.getElementById('friendsTab').classList.add('active');
                document.getElementById('friendsList').classList.add('active');
                showRightPanel('friends');
            }
        }
        
        function showRightPanel(type) {
            currentPanelType = type;
            const panel = document.getElementById('rightPanel');
            const title = document.getElementById('rightPanelTitle');
            const content = document.getElementById('rightPanelContent');
            
            if (type === 'rooms') {
                title.textContent = 'Tüm Odalar';
                loadAllRoomsForPanel();
            } else if (type === 'friends') {
                title.textContent = 'Tüm Arkadaşlar';
                loadAllFriendsForPanel();
            }
            
            panel.classList.add('active');
        }
        
        function closeRightPanel() {
            document.getElementById('rightPanel').classList.remove('active');
            currentPanelType = '';
        }
        
        function loadAllRoomsForPanel() {
            fetch('/api/all_rooms?user_id=' + userId)
            .then(res => res.json())
            .then(rooms => {
                const content = document.getElementById('rightPanelContent');
                content.innerHTML = '';
                
                if (rooms.length === 0) {
                    content.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💬</div><p>Henüz oda yok</p></div>';
                    return;
                }
                
                rooms.forEach(room => {
                    const isPrivate = room.name.includes('_private_');
                    const isGroup = room.name.includes('_group_');
                    
                    const roomItem = document.createElement('div');
                    roomItem.className = 'panel-room-item';
                    roomItem.setAttribute('data-room', room.name);
                    roomItem.onclick = () => joinRoomFromPanel(room.name);
                    
                    const icons = {
                        'Genel': '💬',
                        'Teknoloji': '💻',
                        'Spor': '⚽',
                        'Müzik': '🎵',
                        'Oyun': '🎮'
                    };
                    let icon = '📌';
                    if (isPrivate) icon = '🔒';
                    else if (isGroup) icon = '👥';
                    else icon = icons[room.name] || '📌';
                    
                    let displayName = room.name;
                    if (isGroup) {
                        displayName = room.name.split('_')[1];
                    }
                    
                    roomItem.innerHTML = `
                        <span class="panel-room-icon">${icon}</span>
                        <span class="panel-room-name">${displayName}</span>
                    `;
                    
                    content.appendChild(roomItem);
                });
            });
        }
        
        function loadAllFriendsForPanel() {
            fetch('/api/friends?user_id=' + userId)
            .then(res => res.json())
            .then(friends => {
                const content = document.getElementById('rightPanelContent');
                content.innerHTML = '';
                
                if (friends.length === 0) {
                    content.innerHTML = '<div class="empty-state"><div class="empty-state-icon">👥</div><p>Henüz arkadaşınız yok</p></div>';
                    return;
                }
                
                friends.forEach(friend => {
                    const friendItem = document.createElement('div');
                    friendItem.className = 'panel-friend-item ' + (friend.online ? 'online' : 'offline');
                    friendItem.setAttribute('data-friend-id', friend.user_id);
                    friendItem.onclick = () => startPrivateChatWithFriend(friend.user_id);
                    
                    friendItem.innerHTML = `
                        <span class="panel-friend-icon">👤</span>
                        <div style="flex: 1;">
                            <div class="panel-friend-name">${friend.username}</div>
                            <div class="panel-friend-status">${friend.online ? 'Çevrimiçi' : 'Çevrimdışı'}</div>
                        </div>
                    `;
                    
                    content.appendChild(friendItem);
                });
            });
        }
        
        function joinRoomFromPanel(roomName) {
            joinRoom(roomName);
            closeRightPanel();
        }
        
        function hideMessages() {
            document.getElementById('errorMessage').style.display = 'none';
            document.getElementById('successMessage').style.display = 'none';
        }
        
        function showError(message) {
            const errorDiv = document.getElementById('errorMessage');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            setTimeout(hideMessages, 5000);
        }
        
        function showSuccess(message) {
            const successDiv = document.getElementById('successMessage');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            setTimeout(hideMessages, 3000);
        }
        
        function showGroupError(message) {
            const errorDiv = document.getElementById('groupErrorMessage');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            setTimeout(() => errorDiv.style.display = 'none', 5000);
        }
        
        function showGroupSuccess(message) {
            const successDiv = document.getElementById('groupSuccessMessage');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            setTimeout(() => successDiv.style.display = 'none', 3000);
        }
        
        function handleFileSelect(event) {
            const files = event.target.files;
            selectedFiles = Array.from(files);
            updateSelectedFilesDisplay();
        }
        
        function updateSelectedFilesDisplay() {
            const container = document.getElementById('selectedFiles');
            container.innerHTML = '';
            
            selectedFiles.forEach((file, index) => {
                const fileTag = document.createElement('div');
                fileTag.className = 'file-tag';
                fileTag.innerHTML = `
                    ${getFileIcon(file.type)} ${file.name}
                    <span class="remove-file" onclick="removeFile(${index})">×</span>
                `;
                container.appendChild(fileTag);
            });
        }
        
        function getFileIcon(fileType) {
            if (fileType.startsWith('image/')) return '🖼️';
            if (fileType.startsWith('video/')) return '🎬';
            if (fileType.startsWith('audio/')) return '🎵';
            if (fileType.includes('pdf')) return '📄';
            if (fileType.includes('word') || fileType.includes('document')) return '📝';
            if (fileType.includes('zip') || fileType.includes('rar')) return '📦';
            return '📎';
        }
        
        function removeFile(index) {
            selectedFiles.splice(index, 1);
            updateSelectedFilesDisplay();
        }
        
        function register() {
            const user = document.getElementById('registerUsername').value.trim();
            const email = document.getElementById('registerEmail').value.trim();
            const password = document.getElementById('registerPassword').value;
            const passwordConfirm = document.getElementById('registerPasswordConfirm').value;
            
            if (!user || !email || !password) {
                showError('Tüm alanları doldurun!');
                return;
            }
            
            if (password.length < 6) {
                showError('Şifre en az 6 karakter olmalı!');
                return;
            }
            
            if (password !== passwordConfirm) {
                showError('Şifreler eşleşmiyor!');
                return;
            }
            
            const emailRegex = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
            if (!emailRegex.test(email)) {
                showError('Geçerli bir e-posta adresi girin!');
                return;
            }
            
            fetch('/api/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: user, email, password })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showSuccess('✅ Kayıt başarılı! Giriş yapabilirsiniz.');
                    setTimeout(() => {
                        switchTab('login');
                        document.getElementById('loginUsername').value = user;
                    }, 1500);
                } else {
                    showError(data.message || 'Kayıt başarısız!');
                }
            })
            .catch(() => showError('Bir hata oluştu!'));
        }
        
        function login() {
            const user = document.getElementById('loginUsername').value.trim();
            const pass = document.getElementById('loginPassword').value;
            
            if (!user || !pass) {
                showError('Kullanıcı adı ve şifre girin!');
                return;
            }
            
            fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: user, password: pass })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    username = data.username;
                    userEmail = data.email;
                    userId = data.user_id;
                    isAdmin = data.is_admin || false;
                    
                    document.getElementById('authModal').classList.add('hidden');
                    document.getElementById('mainContainer').classList.add('active');
                    
                    let userInfoText = '👤 ' + username;
                    if (isAdmin) {
                        userInfoText += ' <span class="admin-badge">ADMIN</span>';
                        document.getElementById('adminPanelBtn').style.display = 'block';
                    }
                    document.getElementById('userInfo').innerHTML = userInfoText;
                    
                    document.getElementById('userIdDisplay').textContent = '🔑 ID: ' + userId;
                    initSocket();
                    loadRooms();
                    loadFriends();
                    checkFriendRequests();
                } else {
                    showError(data.message || 'Giriş başarısız!');
                }
            })
            .catch(() => showError('Bir hata oluştu!'));
        }
        
        function logout() {
            // Eğer kayıt devam ediyorsa durdur
            if (isRecording) {
                stopRecording();
            }
            fetch('/api/logout', { method: 'POST' })
            .then(() => location.reload());
        }
        
        function showProfile() {
            document.getElementById('profileUsername').textContent = username;
            document.getElementById('profileEmail').textContent = userEmail;
            document.getElementById('profileUserId').textContent = userId;
            document.getElementById('profileAvatarText').textContent = username.charAt(0).toUpperCase();
            
            let roleText = 'Kullanıcı';
            if (isAdmin) {
                roleText = '👑 Admin';
            }
            document.getElementById('profileRole').textContent = roleText;
            
            fetch('/api/profile')
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const joinDate = new Date(data.created_at);
                    document.getElementById('profileJoinDate').textContent = 
                        'Üyelik: ' + joinDate.toLocaleDateString('tr-TR');
                    
                    // Profil fotoğrafını yükle
                    if (data.profile_picture) {
                        const profileAvatarImage = document.getElementById('profileAvatarImage');
                        const profileAvatarText = document.getElementById('profileAvatarText');
                        
                        profileAvatarImage.src = `/api/files/${data.profile_picture}`;
                        profileAvatarImage.style.display = 'block';
                        profileAvatarText.style.display = 'none';
                    }
                }
            });
            
            document.getElementById('profileModal').classList.add('active');
        }
        
        function closeProfile() {
            document.getElementById('profileModal').classList.remove('active');
            // Formları temizle
            document.getElementById('currentPassword').value = '';
            document.getElementById('newPassword').value = '';
            document.getElementById('confirmPassword').value = '';
        }
        
        function showInbox() {
            loadFriendRequests();
            document.getElementById('inboxModal').classList.add('active');
        }
        
        function closeInbox() {
            document.getElementById('inboxModal').classList.remove('active');
        }
        
        function showAdminPanel() {
            loadAdminUsers();
            loadAdminComplaints();
            switchAdminTab('users');
            document.getElementById('adminModal').classList.add('active');
        }
        
        function closeAdminPanel() {
            document.getElementById('adminModal').classList.remove('active');
        }
        
        function loadAdminUsers() {
            console.log('🔍 Admin kullanıcıları yükleniyor...');
            
            fetch('/api/admin/users')
            .then(res => {
                console.log('📡 Yanıt status:', res.status);
                if (!res.ok) {
                    throw new Error(`HTTP error! status: ${res.status}`);
                }
                return res.json();
            })
            .then(data => {
                console.log('📊 Gelen veri:', data);
                
                const tableBody = document.getElementById('adminUsersTableBody');
                const userCountElement = document.getElementById('adminUserCount');
                const emptyState = document.getElementById('emptyState');
                
                if (!tableBody || !userCountElement || !emptyState) {
                    console.error('❌ HTML elementleri bulunamadı!');
                    console.log('tableBody:', tableBody);
                    console.log('userCountElement:', userCountElement);
                    console.log('emptyState:', emptyState);
                    showError('Admin panel elementleri bulunamadı!');
                    return;
                }
                
                tableBody.innerHTML = '';
                
                // Eğer success false ise hata göster
                if (data && typeof data === 'object' && data.success === false) {
                    console.error('❌ API hatası:', data.message);
                    showError(data.message || 'Kullanıcı listesi alınamadı!');
                    userCountElement.textContent = 'Toplam Kullanıcı: 0';
                    emptyState.style.display = 'block';
                    tableBody.style.display = 'none';
                    return;
                }
                
                // Toplam kullanıcı sayısını göster
                if (data && Array.isArray(data)) {
                    console.log(`✅ ${data.length} kullanıcı bulundu`);
                    userCountElement.textContent = `Toplam Kullanıcı: ${data.length}`;
                    
                    if (data.length === 0) {
                        console.log('📭 Kullanıcı listesi boş');
                        emptyState.style.display = 'block';
                        tableBody.style.display = 'none';
                        return;
                    }
                    
                    emptyState.style.display = 'none';
                    tableBody.style.display = '';
                    
                    data.forEach((user, index) => {
                        console.log(`👤 Kullanıcı işleniyor ${index + 1}/${data.length}:`, user.username);
                        
                        const row = document.createElement('tr');
                        
                        let createdDate = 'Bilinmiyor';
                        if (user.created_at) {
                            try {
                                const date = new Date(user.created_at);
                                createdDate = date.toLocaleDateString('tr-TR', {
                                    year: 'numeric',
                                    month: 'long',
                                    day: 'numeric',
                                    hour: '2-digit',
                                    minute: '2-digit'
                                });
                            } catch (e) {
                                console.error('❌ Tarih formatlama hatası:', e);
                            }
                        }
                        
                        let statusText = user.banned ? '❌ Banlı' : '✅ Aktif';
                        
                        let actionButton = '';
                        if (user.is_admin) {
                            actionButton = '<span style="color: #f39c12; font-weight: bold;">👑 Admin</span>';
                        } else {
                            actionButton = user.banned ? 
                                `<button class="unban-btn" onclick="unbanUser('${user.user_id}')">Ban Kaldır</button>` :
                                `<button class="ban-btn" onclick="banUser('${user.user_id}')">Banla</button>`;
                        }
                        
                        row.innerHTML = `
                            <td>${user.username || 'Bilinmiyor'} ${user.is_admin ? '👑' : ''}</td>
                            <td>${user.user_id || 'Bilinmiyor'}</td>
                            <td>${user.email || 'Bilinmiyor'}</td>
                            <td>${createdDate}</td>
                            <td>${statusText}</td>
                            <td>${actionButton}</td>
                        `;
                        
                        tableBody.appendChild(row);
                    });
                    
                    console.log('✅ Tüm kullanıcılar tabloya eklendi');
                } else {
                    console.error('❌ Geçersiz veri formatı:', typeof data);
                    userCountElement.textContent = 'Toplam Kullanıcı: 0';
                    emptyState.style.display = 'block';
                    tableBody.style.display = 'none';
                    showError('Kullanıcı verileri yüklenemedi!');
                }
            })
            .catch(error => {
                console.error('❌ Admin kullanıcıları yükleme hatası:', error);
                console.error('❌ Hata detayı:', error.message);
                console.error('❌ Hata stack:', error.stack);
                showError('Kullanıcı listesi yüklenirken hata oluştu: ' + error.message);
                
                const userCountElement = document.getElementById('adminUserCount');
                const emptyState = document.getElementById('emptyState');
                const tableBody = document.getElementById('adminUsersTableBody');
                
                if (userCountElement) userCountElement.textContent = 'Toplam Kullanıcı: 0';
                if (emptyState) emptyState.style.display = 'block';
                if (tableBody) tableBody.style.display = 'none';
            });
        }
        
        function banUser(targetUserId) {
            if (!confirm('Bu kullanıcıyı banlamak istediğinizden emin misiniz?')) return;
            
            fetch('/api/admin/ban', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showSuccess('Kullanıcı başarıyla banlandı!');
                    loadAdminUsers();
                } else {
                    showError(data.message || 'Ban işlemi başarısız!');
                }
            });
        }
        
        function unbanUser(targetUserId) {
            if (!confirm('Bu kullanıcının banını kaldırmak istediğinizden emin misiniz?')) return;
            
            fetch('/api/admin/unban', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_user_id: targetUserId })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showSuccess('Kullanıcının banı kaldırıldı!');
                    loadAdminUsers();
                } else {
                    showError(data.message || 'Ban kaldırma işlemi başarısız!');
                }
            });
        }
        
        function showGroupModal() {
            document.getElementById('groupModal').classList.add('active');
        }
        
        function closeGroupModal() {
            document.getElementById('groupModal').classList.remove('active');
            document.getElementById('groupNameInput').value = '';
            document.getElementById('groupUser1Input').value = '';
            document.getElementById('groupUser2Input').value = '';
            document.getElementById('groupErrorMessage').style.display = 'none';
            document.getElementById('groupSuccessMessage').style.display = 'none';
        }
        
        function createGroup() {
            const groupName = document.getElementById('groupNameInput').value.trim();
            const user1Id = document.getElementById('groupUser1Input').value.trim();
            const user2Id = document.getElementById('groupUser2Input').value.trim();
            
            if (!groupName) {
                showGroupError('Grup adı gerekli!');
                return;
            }
            
            if (!user1Id || !user2Id) {
                showGroupError('Her iki kullanıcı ID\\'sini de girin!');
                return;
            }
            
            if (user1Id === userId || user2Id === userId) {
                showGroupError('Kendi ID\\'nizi giremezsiniz!');
                return;
            }
            
            if (user1Id === user2Id) {
                showGroupError('Aynı kullanıcıyı iki kez ekleyemezsiniz!');
                return;
            }
            
            socket.emit('create_group', {
                group_name: groupName,
                user1_id: user1Id,
                user2_id: user2Id,
                creator_id: userId,
                creator_username: username
            });
        }
        
        function sendFriendRequest() {
            const friendId = document.getElementById('friendIdInput').value.trim();
            
            if (!friendId) {
                showError('Lütfen geçerli bir ID girin!');
                return;
            }
            
            if (friendId === userId) {
                showError('Kendinize arkadaşlık isteği gönderemezsiniz!');
                return;
            }
            
            socket.emit('send_friend_request', {
                from_id: userId,
                from_username: username,
                to_id: friendId
            });
            
            document.getElementById('friendIdInput').value = '';
        }
        
        function deleteRoom(roomName) {
            if (!confirm(`"${roomName}" odasını silmek istediğinizden emin misiniz?`)) {
                return;
            }
            
            socket.emit('delete_room', {
                room_name: roomName,
                user_id: userId
            });
        }
        
        function initSocket() {
            const notificationSound = document.getElementById('notificationSound');
            
            function playNotificationSound() {
                // Sesi oynatmadan önce yüklemeyi dene (bazı tarayıcılar için gerekli)
                notificationSound.load();
                notificationSound.play().catch(error => {
                    // Otomatik oynatma hatasını yakala (kullanıcı etkileşimi olmadan oynatılamaz)
                    console.warn('Ses oynatma hatası (kullanıcı etkileşimi gerekli):', error);
                });
            }
            socket = io({
                transports: ['websocket', 'polling'],
                upgrade: true,
                rememberUpgrade: true,
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                reconnectionAttempts: 5
            });
            
            socket.on('connect', () => {
                console.log('✅ Socket bağlandı!');
                socket.emit('register_user', { 
                    username: username,
                    user_id: userId,
                    is_admin: isAdmin
                });
            });
            
            socket.on('user_registered', data => {
                console.log('✅ Kullanıcı socket\\'e kaydedildi');
            });
            
            socket.on('disconnect', () => console.log('❌ Socket bağlantısı kesildi'));
            
            // Kullanıcı giriş/çıkış olayları için ses çalma
            socket.on('user_joined', data => {
                console.log(`[Sistem] ${data.username} sunucuya katıldı.`);
                playNotificationSound();
                // İsteğe bağlı: Kullanıcı listesini güncelle
                loadFriends();
            });
            
            socket.on('user_left', data => {
                console.log(`[Sistem] ${data.username} sunucudan ayrıldı.`);
                playNotificationSound();
                // İsteğe bağlı: Kullanıcı listesini güncelle
                loadFriends();
            });
            
            socket.on('receive_message', data => {
                if (data.room === currentRoom) {
                    displayMessage(data.username, data.message, data.timestamp, data.files, data.profile_picture);
                }
            });
            
            socket.on('room_created', data => {
                addRoomToList(data.name);
                if (currentPanelType === 'rooms') {
                    loadAllRoomsForPanel();
                }
            });
            
            socket.on('private_room_created', data => {
                addRoomToList(data.room, true);
                joinRoom(data.room);
                if (currentPanelType === 'rooms') {
                    loadAllRoomsForPanel();
                }
            });
            
            socket.on('group_created', data => {
                addRoomToList(data.room, false, true);
                joinRoom(data.room);
                closeGroupModal();
                showGroupSuccess('✅ Grup başarıyla oluşturuldu!');
                if (currentPanelType === 'rooms') {
                    loadAllRoomsForPanel();
                }
            });
            
            socket.on('group_creation_failed', data => {
                showGroupError(data.message);
            });
            
            socket.on('friend_request_received', data => {
                checkFriendRequests();
                if (document.getElementById('inboxModal').classList.contains('active')) {
                    loadFriendRequests();
                }
            });
            
            socket.on('friend_request_accepted', data => {
                loadFriends();
                if (currentPanelType === 'friends') {
                    loadAllFriendsForPanel();
                }
                showSuccess(`✅ ${data.friend_username} arkadaş oldu!`);
            });
            
            socket.on('friend_request_rejected', data => {
                showSuccess(`❌ ${data.friend_username} arkadaşlık isteğinizi reddetti.`);
            });
            
            socket.on('friend_added', data => {
                loadFriends();
                if (currentPanelType === 'friends') {
                    loadAllFriendsForPanel();
                }
                showSuccess(`✅ ${data.friend_username} arkadaş eklendi!`);
            });
            
            socket.on('friend_request_sent', data => {
                showSuccess(data.message);
            });
            
            socket.on('room_deleted', data => {
                const roomItem = document.querySelector(`[data-room="${data.room_name}"]`);
                if (roomItem) {
                    roomItem.remove();
                }
                
                if (currentRoom === data.room_name) {
                    joinRoom('Genel');
                }
                
                if (currentPanelType === 'rooms') {
                    loadAllRoomsForPanel();
                }
                
                showSuccess('✅ Oda başarıyla silindi!');
            });
            
            socket.on('room_delete_failed', data => {
                showError(data.message);
            });
            
            socket.on('user_banned', data => {
                showError('❌ Hesabınız admin tarafından banlandı!');
                logout();
            });
            
            socket.on('error_message', data => showError(data.message));
        }
        
        function loadRooms() {
            fetch('/api/rooms?user_id=' + userId)
            .then(res => res.json())
            .then(rooms => {
                const roomsList = document.getElementById('roomsList');
                roomsList.innerHTML = '';
                rooms.forEach(room => {
                    const isPrivate = room.name.includes('_private_');
                    const isGroup = room.name.includes('_group_');
                    addRoomToList(room.name, isPrivate, isGroup);
                });
                setActiveRoom('Genel');
                joinRoom('Genel');
            });
        }
        
        function loadFriends() {
            fetch('/api/friends?user_id=' + userId)
            .then(res => res.json())
            .then(friends => {
                const friendsList = document.getElementById('friendsList');
                friendsList.innerHTML = '';
                
                if (friends.length === 0) {
                    friendsList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">👥</div><p>Henüz arkadaşınız yok</p></div>';
                    return;
                }
                
                friends.forEach(friend => {
                    const friendItem = document.createElement('div');
                    friendItem.className = 'friend-item ' + (friend.online ? 'online' : 'offline');
                    friendItem.setAttribute('data-friend-id', friend.user_id);
                    friendItem.onclick = () => startPrivateChatWithFriend(friend.user_id);
                    
                    friendItem.innerHTML = `
                        <span class="friend-icon">👤</span>
                        <div style="flex: 1;">
                            <div class="friend-name">${friend.username}</div>
                            <div class="friend-status">${friend.online ? 'Çevrimiçi' : 'Çevrimdışı'}</div>
                        </div>
                    `;
                    
                    friendsList.appendChild(friendItem);
                });
            });
        }
        
        function loadFriendRequests() {
            fetch('/api/friend_requests?user_id=' + userId)
            .then(res => res.json())
            .then(requests => {
                const inboxList = document.getElementById('inboxList');
                inboxList.innerHTML = '';
                
                if (requests.length === 0) {
                    inboxList.innerHTML = '<div class="empty-inbox"><div style="font-size: 48px; margin-bottom: 15px;">📭</div><p>Henüz arkadaşlık isteğiniz yok</p></div>';
                    return;
                }
                
                requests.forEach(request => {
                    const requestItem = document.createElement('div');
                    requestItem.className = 'inbox-item';
                    requestItem.innerHTML = `
                        <div class="inbox-user">${request.from_username}</div>
                        <div class="inbox-actions">
                            <button class="inbox-accept-btn" onclick="acceptFriendRequest('${request._id}', '${request.from_id}')">Kabul</button>
                            <button class="inbox-reject-btn" onclick="rejectFriendRequest('${request._id}', '${request.from_id}')">Red</button>
                        </div>
                    `;
                    inboxList.appendChild(requestItem);
                });
            });
        }
        
        function checkFriendRequests() {
            fetch('/api/friend_requests/count?user_id=' + userId)
            .then(res => res.json())
            .then(data => {
                const badge = document.getElementById('inboxBadge');
                if (data.count > 0) {
                    badge.textContent = data.count;
                    badge.style.display = 'inline';
                } else {
                    badge.style.display = 'none';
                }
            });
        }
        
        function acceptFriendRequest(requestId, fromId) {
            socket.emit('accept_friend_request', {
                request_id: requestId,
                from_id: fromId,
                to_id: userId
            });
        }
        
        function rejectFriendRequest(requestId, fromId) {
            socket.emit('reject_friend_request', {
                request_id: requestId,
                from_id: fromId,
                to_id: userId
            });
        }
        
        function startPrivateChatWithFriend(friendId) {
            socket.emit('start_private_chat', {
                from_id: userId,
                to_id: friendId,
                username
            });
            closeRightPanel();
        }
        
        function addRoomToList(roomName, isPrivate = false, isGroup = false) {
            const roomsList = document.getElementById('roomsList');
            const existingRoom = document.querySelector(`[data-room="${roomName}"]`);
            if (existingRoom) return;
            
            const roomItem = document.createElement('div');
            roomItem.className = 'room-item' + (isPrivate ? ' private' : '') + (isGroup ? ' group' : '');
            roomItem.setAttribute('data-room', roomName);
            roomItem.onclick = () => joinRoom(roomName);
            
            const icons = {
                'Genel': '💬',
                'Teknoloji': '💻',
                'Spor': '⚽',
                'Müzik': '🎵',
                'Oyun': '🎮'
            };
            let icon = '📌';
            if (isPrivate) icon = '🔒';
            else if (isGroup) icon = '👥';
            else icon = icons[roomName] || '📌';
            
            let displayName = roomName;
            if (isGroup) {
                displayName = roomName.split('_')[1];
            }
            
            let deleteButton = '';
            if (isAdmin && !isPrivate && !isGroup) {
                deleteButton = `<button class="delete-room-btn" onclick="event.stopPropagation(); deleteRoom('${roomName}')">×</button>`;
            }
            
            roomItem.innerHTML = `<span class="room-icon">${icon}</span><span class="room-name">${displayName}</span>${deleteButton}`;
            roomsList.appendChild(roomItem);
        }
        
        function setActiveRoom(roomName) {
            document.querySelectorAll('.room-item').forEach(item => {
                item.classList.toggle('active', item.getAttribute('data-room') === roomName);
            });
        }
        
        function joinRoom(roomName) {
            if (currentRoom === roomName) return;
            
            if (socket && currentRoom) {
                socket.emit('leave_room', { room: currentRoom, username });
            }
            
            currentRoom = roomName;
            
            if (socket) {
                socket.emit('join_room', { room: roomName, username });
            }
            
            const icons = {
                'Genel': '💬',
                'Teknoloji': '💻',
                'Spor': '⚽',
                'Müzik': '🎵',
                'Oyun': '🎮'
            };
            let icon = '📌';
            let displayName = roomName;
            
            if (roomName.includes('_private_')) {
                icon = '🔒';
            } else if (roomName.includes('_group_')) {
                icon = '👥';
                displayName = roomName.split('_')[1];
            } else {
                icon = icons[roomName] || '📌';
            }
            
            document.getElementById('currentRoomName').innerHTML = `<span class="room-icon">${icon}</span> ${displayName}`;
            setActiveRoom(roomName);
            loadMessages(roomName);
        }
        
        function loadMessages(roomName) {
            fetch(`/api/messages?room=${encodeURIComponent(roomName)}`)
            .then(res => res.json())
            .then(messages => {
                const messagesDiv = document.getElementById('messages');
                messagesDiv.innerHTML = '';
                
                if (messages.length === 0) {
                    let roomDisplayName = roomName;
                    if (roomName.includes('_group_')) {
                        roomDisplayName = roomName.split('_')[1];
                    }
                    messagesDiv.innerHTML = `<div class="empty-state"><div class="empty-state-icon">💬</div><p>${roomDisplayName} odasında henüz mesaj yok. İlk mesajı sen gönder!</p></div>`;
                } else {
                    messages.forEach(msg => displayMessage(msg.username, msg.message, msg.timestamp, msg.files || [], msg.profile_picture, true));
                }
                scrollToBottom();
            });
        }
        
        function displayMessage(user, message, timestamp, files = [], profilePicture = null, isHistory = false) {
            const messagesDiv = document.getElementById('messages');
            const emptyState = messagesDiv.querySelector('.empty-state');
            if (emptyState) emptyState.remove();
            
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message' + (user === username ? ' own' : '');
            
            // Profil avatarı oluştur
            let avatarHtml = '';
            if (profilePicture) {
                avatarHtml = `<img src="/api/files/${profilePicture}" alt="${user}">`;
            } else {
                avatarHtml = user.charAt(0).toUpperCase();
            }
            
            let filesHtml = '';
            if (files && files.length > 0) {
                files.forEach(file => {
                    if (file.file_type === 'image') {
                        filesHtml += `
                            <div class="file-message">
                                <img src="/api/files/${file.file_id}" alt="${file.filename}" style="max-width: 300px; max-height: 300px;">
                                <div class="file-info">
                                    <span class="file-icon">🖼️</span>
                                    <span>${file.filename}</span>
                                    <a href="/api/files/${file.file_id}" download="${file.filename}" class="file-download">İndir</a>
                                </div>
                            </div>
                        `;
                    } else if (file.file_type === 'video') {
                        filesHtml += `
                            <div class="file-message">
                                <video controls style="max-width: 300px; max-height: 300px;">
                                    <source src="/api/files/${file.file_id}" type="${file.mime_type}">
                                    Tarayıcınız video etiketini desteklemiyor.
                                </video>
                                <div class="file-info">
                                    <span class="file-icon">🎬</span>
                                    <span>${file.filename}</span>
                                    <a href="/api/files/${file.file_id}" download="${file.filename}" class="file-download">İndir</a>
                                </div>
                            </div>
                        `;
                    } else if (file.file_type === 'audio') {
                        filesHtml += `
                            <div class="audio-message">
                                <audio controls style="width: 100%; max-width: 300px;">
                                    <source src="/api/files/${file.file_id}" type="${file.mime_type}">
                                    Tarayıcınız ses etiketini desteklemiyor.
                                </audio>
                                <div class="file-info">
                                    <span class="file-icon">🎵</span>
                                    <span>${file.filename}</span>
                                    <a href="/api/files/${file.file_id}" download="${file.filename}" class="file-download">İndir</a>
                                </div>
                            </div>
                        `;
                    } else {
                        filesHtml += `
                            <div class="file-message">
                                <div class="file-info">
                                    <span class="file-icon">${getFileIcon(file.mime_type)}</span>
                                    <span>${file.filename}</span>
                                    <a href="/api/files/${file.file_id}" download="${file.filename}" class="file-download">İndir</a>
                                </div>
                            </div>
                        `;
                    }
                });
            }
            
            messageDiv.innerHTML = `
                <div class="message-avatar">${avatarHtml}</div>
                <div class="message-content-wrapper">
                    <div class="message-content">
                        <div class="username">${user}</div>
                        ${message ? `<div class="message-text">${message}</div>` : ''}
                        ${filesHtml}
                        <div class="timestamp">${timestamp}</div>
                    </div>
                </div>`;
            
            messagesDiv.appendChild(messageDiv);
            if (!isHistory) scrollToBottom();
        }
        
        function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if ((!message && selectedFiles.length === 0) || !socket || !socket.connected || !currentRoom) {
                return;
            }
            
            if (selectedFiles.length > 0) {
                const formData = new FormData();
                formData.append('room', currentRoom);
                formData.append('username', username);
                formData.append('message', message);
                
                selectedFiles.forEach(file => {
                    formData.append('files', file);
                });
                
                fetch('/api/upload_files', {
                    method: 'POST',
                    body: formData
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        socket.emit('send_message', { 
                            username, 
                            message, 
                            room: currentRoom,
                            files: data.files 
                        });
                        input.value = '';
                        selectedFiles = [];
                        updateSelectedFilesDisplay();
                    } else {
                        alert('Dosya yükleme başarısız: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Dosya yükleme hatası:', error);
                    alert('Dosya yükleme sırasında hata oluştu!');
                });
            } else {
                socket.emit('send_message', { username, message, room: currentRoom });
                input.value = '';
            }
        }
        
        function createRoom() {
            const input = document.getElementById('newRoomInput');
            const roomName = input.value.trim();
            
            if (roomName) {
                fetch('/api/create_room', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: roomName })
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        input.value = '';
                        socket.emit('new_room', { name: roomName });
                        addRoomToList(roomName, false);
                        joinRoom(roomName);
                    } else {
                        alert(data.message || 'Oda oluşturulamadı!');
                    }
                });
            }
        }
        
        function startPrivateChat() {
            const input = document.getElementById('privateUserIdInput');
            const targetUserId = input.value.trim();
            
            if (!targetUserId) {
                alert('Lütfen geçerli bir ID girin!');
                return;
            }
            
            if (targetUserId === userId) {
                alert('Kendinizle özel sohbet yapamazsınız!');
                return;
            }
            
            socket.emit('start_private_chat', {
                from_id: userId,
                to_id: targetUserId,
                username
            });
            
            input.value = '';
        }
        
        function copyUserId() {
            navigator.clipboard.writeText(userId).then(() => {
                alert('ID kopyalandı: ' + userId);
            }).catch(() => {
                const textarea = document.createElement('textarea');
                textarea.value = userId;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                alert('ID kopyalandı: ' + userId);
            });
        }
        
        function scrollToBottom() {
            const messagesDiv = document.getElementById('messages');
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        // Tema ayarlarını yükle
        function loadThemeSettings() {
            const savedTheme = localStorage.getItem('theme') || 'light';
            document.documentElement.setAttribute('data-theme', savedTheme);
            updateThemeButton(savedTheme);
        }
        
        // Tema değiştirme fonksiyonu
        function toggleTheme() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateThemeButton(newTheme);
        }
        
        // Tema butonunu güncelle
        function updateThemeButton(theme) {
            const themeBtn = document.getElementById('themeToggleBtn');
            if (themeBtn) {
                themeBtn.textContent = theme === 'dark' ? '☀️' : '🌙';
                themeBtn.title = theme === 'dark' ? 'Aydınlık Moda Geç' : 'Karanlık Moda Geç';
            }
        }
        
        // Şikayet modal'ını göster
        function showComplaintModal() {
            document.getElementById('complaintModal').classList.add('active');
        }
        
        // Şikayet modal'ını kapat
        function closeComplaintModal() {
            document.getElementById('complaintModal').classList.remove('active');
            document.getElementById('complaintTargetUsername').value = '';
            document.getElementById('complaintReason').value = '';
            document.getElementById('complaintErrorMessage').style.display = 'none';
            document.getElementById('complaintSuccessMessage').style.display = 'none';
        }
        
        // Şikayet gönderme fonksiyonu
        function submitComplaint() {
            console.log('🚨 Şikayet gönderme işlemi başlatılıyor...');
            
            const targetUsername = document.getElementById('complaintTargetUsername').value.trim();
            const reason = document.getElementById('complaintReason').value.trim();
            
            console.log('📝 Şikayet bilgileri:');
            console.log('  - Hedef kullanıcı:', targetUsername);
            console.log('  - Giriş yapan kullanıcı:', username);
            console.log('  - Sebep:', reason);
            
            if (!targetUsername || !reason) {
                console.log('❌ Eksik alanlar tespit edildi');
                showComplaintError('Lütfen tüm alanları doldurun!');
                return;
            }
            
            if (targetUsername === username) {
                console.log('❌ Kullanıcı kendinden şikayet etmeye çalıştı');
                showComplaintError('Kendinizden şikayet edemezsiniz!');
                return;
            }
            
            console.log('📡 Şikayet gönderiliyor...');
            
            fetch('/api/submit_complaint', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_username: targetUsername,
                    reason: reason
                })
            })
            .then(res => {
                console.log('📡 Sunucu yanıtı status:', res.status);
                if (!res.ok) {
                    throw new Error(`HTTP error! status: ${res.status}`);
                }
                return res.json();
            })
            .then(data => {
                console.log('📊 Şikayet yanıtı:', data);
                
                if (data.success) {
                    console.log('✅ Şikayet başarıyla gönderildi');
                    showComplaintSuccess(data.message);
                    setTimeout(() => {
                        closeComplaintModal();
                    }, 2000);
                } else {
                    console.log('❌ Şikayet gönderilemedi:', data.message);
                    showComplaintError(data.message || 'Şikayet gönderilemedi!');
                }
            })
            .catch(error => {
                console.error('❌ Şikayet gönderme hatası:', error);
                console.error('❌ Hata detayı:', error.message);
                showComplaintError('Şikayet gönderilirken bir hata oluştu: ' + error.message);
            });
        }
        
        // Şikayet hatası göster
        function showComplaintError(message) {
            const errorDiv = document.getElementById('complaintErrorMessage');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            setTimeout(() => errorDiv.style.display = 'none', 5000);
        }
        
        // Şikayet başarı göster
        function showComplaintSuccess(message) {
            const successDiv = document.getElementById('complaintSuccessMessage');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            setTimeout(() => successDiv.style.display = 'none', 3000);
        }
        
        // Admin sekmesi değiştirme
        function switchAdminTab(tab) {
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            
            if (tab === 'users') {
                document.getElementById('usersTab').classList.add('active');
                document.getElementById('adminUsersList').style.display = 'block';
                document.getElementById('adminComplaintsList').style.display = 'none';
            } else {
                document.getElementById('complaintsTab').classList.add('active');
                document.getElementById('adminUsersList').style.display = 'none';
                document.getElementById('adminComplaintsList').style.display = 'block';
                loadAdminComplaints();
            }
        }
        
        // Admin şikayetlerini yükle
        function loadAdminComplaints() {
            fetch('/api/admin/complaints')
            .then(res => res.json())
            .then(complaints => {
                const complaintsList = document.getElementById('complaintsList');
                const emptyState = document.getElementById('complaintsEmptyState');
                
                complaintsList.innerHTML = '';
                
                if (complaints.length === 0) {
                    emptyState.style.display = 'block';
                    return;
                }
                
                emptyState.style.display = 'none';
                
                complaints.forEach(complaint => {
                    const complaintItem = document.createElement('div');
                    complaintItem.className = 'complaint-item';
                    
                    const createdDate = new Date(complaint.created_at).toLocaleString('tr-TR');
                    const statusClass = complaint.status;
                    const statusText = complaint.status === 'pending' ? 'Beklemede' : 
                                      complaint.status === 'resolved' ? 'Çözüldü' : 'Reddedildi';
                    
                    let adminNotes = '';
                    if (complaint.admin_notes) {
                        adminNotes = `<div style="margin-top: 10px; padding: 10px; background: var(--bg-tertiary); border-radius: 6px;">
                            <strong>Admin Notu:</strong> ${complaint.admin_notes}
                        </div>`;
                    }
                    
                    complaintItem.innerHTML = `
                        <div class="complaint-header-info">
                            <div class="complaint-target">🎯 ${complaint.target_username}</div>
                            <div class="complaint-status ${statusClass}">${statusText}</div>
                        </div>
                        <div class="complaint-content">
                            <div class="complaint-reason"><strong>Şikayet Eden:</strong> ${complaint.complainant_username}</div>
                            <div class="complaint-reason">${complaint.reason}</div>
                            ${adminNotes}
                        </div>
                        <div class="complaint-meta">
                            <span>📅 ${createdDate}</span>
                            ${complaint.resolved_by ? `<span>✅ ${complaint.resolved_by}</span>` : ''}
                        </div>
                        ${complaint.status === 'pending' ? `
                            <div class="complaint-actions">
                                <button class="complaint-action-btn resolve-btn" onclick="resolveComplaint('${complaint.complaint_id}', 'resolve')">✅ Çöz</button>
                                <button class="complaint-action-btn dismiss-btn" onclick="resolveComplaint('${complaint.complaint_id}', 'dismiss')">❌ Reddet</button>
                            </div>
                        ` : ''}
                    `;
                    
                    complaintsList.appendChild(complaintItem);
                });
            })
            .catch(error => {
                console.error('Şikayetler yüklenemedi:', error);
                showError('Şikayetler yüklenirken hata oluştu!');
            });
        }
        
        // Şikayeti çöz/reddet
        function resolveComplaint(complaintId, action) {
            const adminNotes = prompt(`Şikayeti ${action === 'resolve' ? 'çözmek' : 'reddetmek'} için bir not girin:`);
            
            fetch('/api/admin/resolve_complaint', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    complaint_id: complaintId,
                    action: action,
                    admin_notes: adminNotes || ''
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    showSuccess(data.message);
                    loadAdminComplaints();
                } else {
                    showError(data.message || 'İşlem başarısız!');
                }
            })
            .catch(() => showError('Bir hata oluştu!'));
        }
        
        // Global functions for inline event handlers
        window.acceptFriendRequest = acceptFriendRequest;
        window.rejectFriendRequest = rejectFriendRequest;
        window.deleteRoom = deleteRoom;
        window.removeFile = removeFile;
        window.banUser = banUser;
        window.unbanUser = unbanUser;
        window.resolveComplaint = resolveComplaint;
    </script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '')
        
        if not username or not email or not password:
            return jsonify({'success': False, 'message': 'Tüm alanları doldurun!'})
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Şifre en az 6 karakter olmalı!'})
        
        existing_user = users_collection.find_one({'$or': [{'username': username}, {'email': email}]})
        if existing_user:
            if existing_user.get('username') == username:
                return jsonify({'success': False, 'message': 'Bu kullanıcı adı zaten kullanılıyor!'})
            else:
                return jsonify({'success': False, 'message': 'Bu e-posta adresi zaten kullanılıyor!'})
        
        # Kalıcı kullanıcı ID'si oluştur
        user_id = generate_user_id(email)
        
        # Admin kontrolü - belirtilen e-posta admin olacak
        is_admin = (email.lower() == ADMIN_EMAIL.lower())
        
        hashed_password = hash_password(password)
        user_doc = {
            'username': username,
            'email': email,
            'password': hashed_password,
            'user_id': user_id,
            'is_admin': is_admin,
            'created_at': datetime.now()
        }
        
        users_collection.insert_one(user_doc)
        logger.info(f'✅ Yeni kullanıcı kaydedildi: {username}, ID: {user_id}, Admin: {is_admin}')
        
        return jsonify({'success': True, 'message': 'Kayıt başarılı!'})
    
    except Exception as e:
        logger.error(f'❌ Kayıt hatası: {e}')
        return jsonify({'success': False, 'message': 'Bir hata oluştu!'})

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Kullanıcı adı ve şifre girin!'})
        
        # Ban kontrolü
        user = users_collection.find_one({'username': username})
        if user and is_user_banned(user.get('user_id', '')):
            return jsonify({'success': False, 'message': 'Hesabınız admin tarafından banlandı!'})
        
        if not user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        hashed_password = hash_password(password)
        if user['password'] != hashed_password:
            return jsonify({'success': False, 'message': 'Şifre hatalı!'})
        
        session['username'] = user['username']
        session['email'] = user['email']
        session['user_id'] = user['user_id']
        session['is_admin'] = user.get('is_admin', False)
        
        logger.info(f'✅ Kullanıcı giriş yaptı: {username}, ID: {user["user_id"]}, Admin: {user.get("is_admin", False)}')
        
        return jsonify({
            'success': True,
            'username': user['username'],
            'email': user['email'],
            'user_id': user['user_id'],
            'is_admin': user.get('is_admin', False)
        })
    
    except Exception as e:
        logger.error(f'❌ Giriş hatası: {e}')
        return jsonify({'success': False, 'message': 'Bir hata oluştu!'})

@app.route('/api/logout', methods=['POST'])
def logout_route():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/profile')
def get_profile():
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Oturum bulunamadı!'})
        
        user = users_collection.find_one({'username': username})
        if not user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        return jsonify({
            'success': True,
            'username': user['username'],
            'email': user['email'],
            'user_id': user['user_id'],
            'is_admin': user.get('is_admin', False),
            'created_at': user['created_at'].isoformat(),
            'profile_picture': user.get('profile_picture', None)
        })
    
    except Exception as e:
        logger.error(f'❌ Profil hatası: {e}')
        return jsonify({'success': False, 'message': 'Bir hata oluştu!'})

@app.route('/api/upload_profile_picture', methods=['POST'])
def upload_profile_picture():
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Oturum bulunamadı!'})
        
        user = users_collection.find_one({'username': username})
        if not user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        if 'profile_picture' not in request.files:
            return jsonify({'success': False, 'message': 'Profil fotoğrafı bulunamadı!'})
        
        file = request.files['profile_picture']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'Dosya seçilmedi!'})
        
        # Sadece resim dosyalarına izin ver
        if not allowed_file(file.filename) or not file.content_type.startswith('image/'):
            return jsonify({'success': False, 'message': 'Sadece resim dosyaları yüklenebilir!'})
        
        # Dosya boyutu kontrolü (5MB)
        file.seek(0, 2)  # End of file
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_size > 5 * 1024 * 1024:
            return jsonify({'success': False, 'message': 'Profil fotoğrafı maksimum 5MB olabilir!'})
        
        # Dosya içeriğini oku
        file_content = file.read()
        file_id = str(uuid.uuid4())
        
        # Profil fotoğrafını veritabanına kaydet
        profile_picture_doc = {
            'file_id': file_id,
            'filename': secure_filename(file.filename),
            'file_type': 'image',
            'mime_type': file.content_type,
            'file_size': file_size,
            'file_content': file_content,
            'uploaded_by': username,
            'uploaded_at': datetime.now(),
            'is_profile_picture': True
        }
        
        files_collection.insert_one(profile_picture_doc)
        
        # Kullanıcının profil fotoğrafını güncelle
        users_collection.update_one(
            {'username': username},
            {'$set': {'profile_picture': file_id}}
        )
        
        logger.info(f'✅ Profil fotoğrafı yüklendi: {username}, File ID: {file_id}')
        
        return jsonify({
            'success': True, 
            'message': 'Profil fotoğrafı başarıyla yüklendi!',
            'profile_picture': file_id
        })
    
    except Exception as e:
        logger.error(f'❌ Profil fotoğrafı yükleme hatası: {e}')
        return jsonify({'success': False, 'message': 'Profil fotoğrafı yükleme başarısız!'})

@app.route('/api/change_password', methods=['POST'])
def change_password():
    try:
        # Oturum kontrolü
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Oturum bulunamadı! Lütfen tekrar giriş yapın.'})

        # Kullanıcıyı veritabanından bul
        user = users_collection.find_one({'username': username})
        if not user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})

        # İstekten gelen verileri al
        data = request.json
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')

        # Boş alan kontrolü
        if not current_password or not new_password or not confirm_password:
            return jsonify({'success': False, 'message': 'Tüm alanları doldurun!'})

        # Yeni şifre uzunluğu kontrolü
        if len(new_password) < 6:
            return jsonify({'success': False, 'message': 'Yeni şifre en az 6 karakter olmalı!'})

        # Yeni şifre ve onay eşleşmesi kontrolü
        if new_password != confirm_password:
            return jsonify({'success': False, 'message': 'Yeni şifreler eşleşmiyor!'})

        # Mevcut şifrenin doğruluğunu kontrol et
        current_hashed_password = hash_password(current_password)
        if user['password'] != current_hashed_password:
            return jsonify({'success': False, 'message': 'Mevcut şifre hatalı!'})

        # Yeni şifreyi hash'le ve güncelle
        new_hashed_password = hash_password(new_password)
        users_collection.update_one(
            {'username': username},
            {'$set': {'password': new_hashed_password}}
        )

        logger.info(f'✅ Şifre değiştirildi: {username}')
        return jsonify({'success': True, 'message': 'Şifre başarıyla değiştirildi!'})

    except Exception as e:
        logger.error(f'❌ Şifre değiştirme hatası: {e}')
        return jsonify({'success': False, 'message': 'Şifre değiştirme sırasında bir hata oluştu!'})


@app.route('/api/user_profile/<username>')
def get_user_profile(username):
    try:
        user = users_collection.find_one({'username': username})
        if not user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        return jsonify({
            'success': True,
            'username': user['username'],
            'user_id': user['user_id'],
            'profile_picture': user.get('profile_picture', None),
            'is_admin': user.get('is_admin', False)
        })
    
    except Exception as e:
        logger.error(f'❌ Kullanıcı profili getirme hatası: {e}')
        return jsonify({'success': False, 'message': 'Bir hata oluştu!'})

@app.route('/api/submit_complaint', methods=['POST'])
def submit_complaint():
    try:
        logger.info('🚨 Şikayet gönderme isteği alındı')
        
        username = session.get('username')
        user_id = session.get('user_id')
        
        logger.info(f'👤 Şikayet eden kullanıcı: {username} (ID: {user_id})')
        
        if not username:
            logger.warning('❌ Oturum bulunamadı')
            return jsonify({'success': False, 'message': 'Oturum bulunamadı! Lütfen tekrar giriş yapın.'})
        
        if not request.is_json:
            logger.warning('❌ Geçersiz JSON isteği')
            return jsonify({'success': False, 'message': 'Geçersiz istek formatı!'})
        
        data = request.json
        logger.debug(f'📝 Gelen veri: {data}')
        
        target_username = data.get('target_username', '').strip()
        reason = data.get('reason', '').strip()
        
        logger.info(f'🎯 Şikayet hedefi: {target_username}')
        logger.info(f'📝 Şikayet nedeni: {reason[:50] if reason else "Boş"}...')
        
        if not target_username or not reason:
            logger.warning('❌ Eksik parametreler')
            return jsonify({
                'success': False, 
                'message': 'Şikayet edilen kullanıcı adı ve şikayet nedeni gereklidir!'
            })
        
        # Kendinden şikayet edemez
        if target_username == username:
            logger.warning(f'❌ {username} kendinden şikayet etmeye çalıştı')
            return jsonify({'success': False, 'message': 'Kendinizden şikayet edemezsiniz!'})
        
        # Hedef kullanıcıyı kontrol et
        try:
            target_user = users_collection.find_one({'username': target_username})
            logger.info(f'🔍 Hedef kullanıcı arandı: {target_username}')
            
            if not target_user:
                logger.warning(f'❌ Hedef kullanıcı bulunamadı: {target_username}')
                return jsonify({'success': False, 'message': 'Şikayet edilen kullanıcı bulunamadı!'})
            
            logger.info(f'✅ Hedef kullanıcı bulundu: {target_username} (ID: {target_user["user_id"]})')
            
        except Exception as find_error:
            logger.error(f'❌ Kullanıcı arama hatası: {find_error}')
            return jsonify({'success': False, 'message': 'Kullanıcı kontrolü sırasında hata oluştu!'})
        
        # Şikayet oluştur
        try:
            complaint_id = str(uuid.uuid4())
            complaint_doc = {
                'complaint_id': complaint_id,
                'complainant_username': username,
                'complainant_user_id': user_id,
                'target_username': target_username,
                'target_user_id': target_user['user_id'],
                'reason': reason,
                'status': 'pending',
                'created_at': datetime.now(),
                'admin_notes': None,
                'resolved_at': None,
                'resolved_by': None
            }
            
            logger.info(f'💾 Şikayet veritabanına ekleniyor: {complaint_id}')
            complaints_collection.insert_one(complaint_doc)
            
            logger.info(f'🚨 Şikayet başarıyla oluşturuldu: {username} -> {target_username}, ID: {complaint_id}')
            
            return jsonify({
                'success': True, 
                'message': 'Şikayetiniz başarıyla gönderildi. Adminler tarafından incelenecektir.',
                'complaint_id': complaint_id
            })
            
        except Exception as insert_error:
            logger.error(f'❌ Şikayet ekleme hatası: {insert_error}')
            return jsonify({'success': False, 'message': 'Şikayet kaydedilirken veritabanı hatası oluştu!'})
    
    except Exception as e:
        logger.error(f'❌ Şikayet gönderme genel hatası: {e}')
        return jsonify({'success': False, 'message': 'Şikayet gönderilirken beklenmedik bir hata oluştu!'})

@app.route('/api/admin/complaints')
def get_admin_complaints():
    try:
        if not session.get('is_admin'):
            return jsonify({'success': False, 'message': 'Yetkisiz erişim!'})
        
        # Tüm şikayetleri getir (en yeni ilk)
        complaints = list(complaints_collection.find({}).sort('created_at', DESCENDING))
        
        # ObjectId'yi string'e çevir ve tarihleri formatla
        for complaint in complaints:
            complaint['_id'] = str(complaint['_id'])
            if 'created_at' in complaint:
                complaint['created_at'] = complaint['created_at'].isoformat()
            if 'resolved_at' in complaint and complaint['resolved_at']:
                complaint['resolved_at'] = complaint['resolved_at'].isoformat()
        
        logger.info(f'✅ Admin için {len(complaints)} şikayet getirildi')
        return jsonify(complaints)
    
    except Exception as e:
        logger.error(f'❌ Şikayetler getirme hatası: {e}')
        return jsonify([])

@app.route('/api/admin/resolve_complaint', methods=['POST'])
def resolve_complaint():
    try:
        if not session.get('is_admin'):
            return jsonify({'success': False, 'message': 'Yetkisiz erişim!'})
        
        data = request.json
        complaint_id = data.get('complaint_id')
        action = data.get('action')  # 'resolve', 'dismiss'
        admin_notes = data.get('admin_notes', '')
        
        if not complaint_id or not action:
            return jsonify({'success': False, 'message': 'Geçersiz istek!'})
        
        # Şikayeti bul
        complaint = complaints_collection.find_one({'complaint_id': complaint_id})
        if not complaint:
            return jsonify({'success': False, 'message': 'Şikayet bulunamadı!'})
        
        # Şikayeti güncelle
        update_data = {
            'status': action,
            'admin_notes': admin_notes,
            'resolved_at': datetime.now(),
            'resolved_by': session.get('username')
        }
        
        complaints_collection.update_one(
            {'complaint_id': complaint_id},
            {'$set': update_data}
        )
        
        logger.info(f'✅ Şikayet güncellendi: {complaint_id}, İşlem: {action}, Admin: {session.get("username")}')
        
        action_text = 'çözüldü' if action == 'resolve' else 'reddedildi'
        return jsonify({
            'success': True, 
            'message': f'Şikayet başarıyla {action_text}!'
        })
    
    except Exception as e:
        logger.error(f'❌ Şikayet çözme hatası: {e}')
        return jsonify({'success': False, 'message': 'İşlem başarısız!'})

@app.route('/api/admin/users')
def get_admin_users():
    try:
        # Sadece admin kullanıcılar erişebilsin
        if not session.get('is_admin'):
            logger.warning(f'❌ Yetkisiz erişim denemesi: {session.get("username", "Bilinmeyen")}')
            return jsonify({'success': False, 'message': 'Yetkisiz erişim!'})
        
        logger.info(f'🔍 Admin kullanıcı listesi getiriliyor. Admin: {session.get("username")}')
        
        # Tüm kullanıcıları getir (şifre hariç)
        try:
            users = list(users_collection.find(
                {}, 
                {'password': 0}  # Şifre alanını hariç tut
            ).sort('created_at', DESCENDING))
            
            logger.info(f'📊 MongoDB\'den {len(users)} kullanıcı bulundu')
            
        except Exception as db_error:
            logger.error(f'❌ MongoDB sorgu hatası: {db_error}')
            return jsonify([])
        
        # Ban durumunu kontrol et ve kullanıcı listesine ekle
        for user in users:
            try:
                user['banned'] = is_user_banned(user.get('user_id', ''))
                # ObjectId'yi string'e çevir (JSON serialization için)
                user['_id'] = str(user['_id'])
                
                # Eksik alanları kontrol et ve varsayılan değerler ata
                if 'user_id' not in user:
                    user['user_id'] = 'Bilinmiyor'
                if 'username' not in user:
                    user['username'] = 'Bilinmiyor'
                if 'email' not in user:
                    user['email'] = 'Bilinmiyor'
                if 'created_at' not in user:
                    user['created_at'] = datetime.now()
                if 'is_admin' not in user:
                    user['is_admin'] = False
                    
                logger.debug(f'👤 Kullanıcı işlendi: {user.get("username", "Bilinmeyen")} (ID: {user.get("user_id", "Bilinmiyor")})')
                
            except Exception as user_error:
                logger.error(f'❌ Kullanıcı işleme hatası: {user_error}')
                continue
        
        logger.info(f'✅ Admin paneli için {len(users)} kullanıcı getirildi')
        return jsonify(users)
        
    except Exception as e:
        logger.error(f'❌ Admin kullanıcı listesi genel hatası: {e}')
        return jsonify({'success': False, 'message': 'Kullanıcı listesi alınamadı!'})

@app.route('/api/admin/ban', methods=['POST'])
def ban_user():
    try:
        if not session.get('is_admin'):
            return jsonify({'success': False, 'message': 'Yetkisiz işlem!'})
        
        data = request.json
        target_user_id = data.get('target_user_id')
        
        if not target_user_id:
            return jsonify({'success': False, 'message': 'Geçersiz kullanıcı ID!'})
        
        # Kendini banlayamaz
        if target_user_id == session.get('user_id'):
            return jsonify({'success': False, 'message': 'Kendinizi banlayamazsınız!'})
        
        # Kullanıcıyı bul
        target_user = users_collection.find_one({'user_id': target_user_id})
        if not target_user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        # Diğer adminleri banlayamaz
        if target_user.get('is_admin'):
            return jsonify({'success': False, 'message': 'Diğer adminleri banlayamazsınız!'})
        
        # Zaten banlı mı kontrol et
        if is_user_banned(target_user_id):
            return jsonify({'success': False, 'message': 'Kullanıcı zaten banlı!'})
        
        # Ban kaydı oluştur
        banned_users_collection.insert_one({
            'user_id': target_user_id,
            'username': target_user.get('username', 'Unknown'),
            'banned_by': session.get('user_id'),
            'banned_at': datetime.now(),
            'reason': 'Admin tarafından banlandı'
        })
        
        logger.info(f'🔨 Kullanıcı banlandı: {target_user_id}, Admin: {session.get("user_id")}')
        
        # Banlanan kullanıcı çevrimiçi ise bağlantısını kes
        target_socket_id = None
        for sid, user_data in active_users.items():
            if user_data.get('user_id') == target_user_id:
                target_socket_id = sid
                break
        
        if target_socket_id:
            socketio.emit('user_banned', {}, to=target_socket_id)
        
        return jsonify({'success': True, 'message': 'Kullanıcı başarıyla banlandı!'})
    
    except Exception as e:
        logger.error(f'❌ Ban işlemi hatası: {e}')
        return jsonify({'success': False, 'message': 'Ban işlemi başarısız!'})

@app.route('/api/admin/unban', methods=['POST'])
def unban_user():
    try:
        if not session.get('is_admin'):
            return jsonify({'success': False, 'message': 'Yetkisiz işlem!'})
        
        data = request.json
        target_user_id = data.get('target_user_id')
        
        if not target_user_id:
            return jsonify({'success': False, 'message': 'Geçersiz kullanıcı ID!'})
        
        # Kullanıcıyı bul
        target_user = users_collection.find_one({'user_id': target_user_id})
        if not target_user:
            return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı!'})
        
        # Ban kaydını sil
        result = banned_users_collection.delete_one({'user_id': target_user_id})
        
        if result.deleted_count > 0:
            logger.info(f'🔓 Kullanıcı banı kaldırıldı: {target_user_id}, Admin: {session.get("user_id")}')
            return jsonify({'success': True, 'message': 'Kullanıcının banı kaldırıldı!'})
        else:
            return jsonify({'success': False, 'message': 'Kullanıcı zaten banlı değil!'})
    
    except Exception as e:
        logger.error(f'❌ Ban kaldırma hatası: {e}')
        return jsonify({'success': False, 'message': 'Ban kaldırma işlemi başarısız!'})

@app.route('/api/upload_files', methods=['POST'])
def upload_files():
    try:
        if 'files' not in request.files:
            return jsonify({'success': False, 'message': 'Dosya bulunamadı!'})
        
        files = request.files.getlist('files')
        room = request.form.get('room', 'Genel')
        username = request.form.get('username', 'Anonim')
        
        if not files or all(file.filename == '' for file in files):
            return jsonify({'success': False, 'message': 'Geçersiz dosya!'})
        
        uploaded_files = []
        
        for file in files:
            if file and file.filename:
                file_type = allowed_file(file.filename)
                if not file_type:
                    return jsonify({'success': False, 'message': f'Geçersiz dosya türü: {file.filename}'})
                
                # Dosya boyutu kontrolü (16MB)
                file.seek(0, 2)  # End of file
                file_size = file.tell()
                file.seek(0)  # Reset file pointer
                
                if file_size > 16 * 1024 * 1024:
                    return jsonify({'success': False, 'message': f'Dosya boyutu çok büyük: {file.filename}'})
                
                # Dosya içeriğini oku
                file_content = file.read()
                file_id = str(uuid.uuid4())
                
                # Dosyayı veritabanına kaydet
                file_doc = {
                    'file_id': file_id,
                    'filename': secure_filename(file.filename),
                    'file_type': file_type,
                    'mime_type': file.content_type,
                    'file_size': file_size,
                    'file_content': file_content,
                    'uploaded_by': username,
                    'uploaded_at': datetime.now(),
                    'room': room
                }
                
                files_collection.insert_one(file_doc)
                
                uploaded_files.append({
                    'file_id': file_id,
                    'filename': secure_filename(file.filename),
                    'file_type': file_type,
                    'mime_type': file.content_type,
                    'file_size': file_size
                })
        
        logger.info(f'✅ Dosya yüklendi: {len(uploaded_files)} dosya, Kullanıcı: {username}, Oda: {room}')
        return jsonify({'success': True, 'files': uploaded_files})
    
    except Exception as e:
        logger.error(f'❌ Dosya yükleme hatası: {e}')
        return jsonify({'success': False, 'message': 'Dosya yükleme başarısız!'})

@app.route('/api/files/<file_id>')
def get_file(file_id):
    try:
        file_doc = files_collection.find_one({'file_id': file_id})
        if not file_doc:
            return 'Dosya bulunamadı', 404
        
        # Dosya içeriğini döndür
        response = app.response_class(
            file_doc['file_content'],
            mimetype=file_doc['mime_type']
        )
        response.headers.set('Content-Disposition', 'inline', filename=file_doc['filename'])
        return response
    
    except Exception as e:
        logger.error(f'❌ Dosya getirme hatası: {e}')
        return 'Dosya bulunamadı', 404

@app.route('/api/rooms')
def get_rooms():
    try:
        user_id = request.args.get('user_id')
        
        # Genel odaları getir
        public_rooms = list(rooms_collection.find(
            {'type': {'$ne': 'group'}}, 
            {'_id': 0, 'name': 1}
        ).sort('name', ASCENDING))
        
        # Kullanıcının üye olduğu grup odalarını getir
        user_groups = list(rooms_collection.find(
            {'type': 'group', 'members': user_id},
            {'_id': 0, 'name': 1}
        ).sort('name', ASCENDING))
        
        # Tüm odaları birleştir
        all_rooms = public_rooms + user_groups
        
        return jsonify(all_rooms)
    except Exception as e:
        logger.error(f'❌ Oda listesi hatası: {e}')
        return jsonify([])

@app.route('/api/all_rooms')
def get_all_rooms():
    try:
        user_id = request.args.get('user_id')
        
        # Tüm genel odaları getir
        public_rooms = list(rooms_collection.find(
            {'type': {'$ne': 'group'}}, 
            {'_id': 0, 'name': 1}
        ).sort('name', ASCENDING))
        
        # Kullanıcının üye olduğu grup odalarını getir
        user_groups = list(rooms_collection.find(
            {'type': 'group', 'members': user_id},
            {'_id': 0, 'name': 1}
        ).sort('name', ASCENDING))
        
        # Tüm odaları birleştir
        all_rooms = public_rooms + user_groups
        
        return jsonify(all_rooms)
    except Exception as e:
        logger.error(f'❌ Tüm odalar listesi hatası: {e}')
        return jsonify([])

@app.route('/api/friends')
def get_friends():
    try:
        user_id = request.args.get('user_id')
        
        # Arkadaşlıkları getir
        friendships = list(friendships_collection.find({
            '$or': [
                {'user_id': user_id},
                {'friend_id': user_id}
            ]
        }))
        
        friends = []
        for friendship in friendships:
            if friendship['user_id'] == user_id:
                friend_id = friendship['friend_id']
            else:
                friend_id = friendship['user_id']
            
            # Kullanıcı bilgilerini getir
            friend_user = users_collection.find_one({'user_id': friend_id})
            if friend_user:
                # Çevrimiçi durumunu kontrol et
                online = any(user_data.get('user_id') == friend_id for user_data in active_users.values())
                
                friends.append({
                    'user_id': friend_id,
                    'username': friend_user['username'],
                    'online': online
                })
        
        return jsonify(friends)
    except Exception as e:
        logger.error(f'❌ Arkadaş listesi hatası: {e}')
        return jsonify([])

@app.route('/api/friend_requests')
def get_friend_requests():
    try:
        user_id = request.args.get('user_id')
        
        requests = list(friend_requests_collection.find({
            'to_id': user_id,
            'status': 'pending'
        }).sort('created_at', DESCENDING))
        
        # ObjectId'yi string'e çevir
        for req in requests:
            req['_id'] = str(req['_id'])
        
        return jsonify(requests)
    except Exception as e:
        logger.error(f'❌ Arkadaşlık istekleri hatası: {e}')
        return jsonify([])

@app.route('/api/friend_requests/count')
def get_friend_requests_count():
    try:
        user_id = request.args.get('user_id')
        
        count = friend_requests_collection.count_documents({
            'to_id': user_id,
            'status': 'pending'
        })
        
        return jsonify({'count': count})
    except Exception as e:
        logger.error(f'❌ Arkadaşlık istekleri sayısı hatası: {e}')
        return jsonify({'count': 0})

@app.route('/api/create_room', methods=['POST'])
def create_room():
    data = request.json
    room_name = data.get('name', '').strip()
    
    if not room_name:
        return jsonify({'success': False, 'message': 'Oda adı boş olamaz!'})
    
    try:
        rooms_collection.insert_one({
            'name': room_name, 
            'type': 'public',
            'created_at': datetime.now(),
            'created_by': session.get('user_id', 'unknown')
        })
        return jsonify({'success': True, 'name': room_name})
    except Exception as e:
        return jsonify({'success': False, 'message': 'Bu oda zaten mevcut!'})

@app.route('/api/messages')
def get_messages():
    room = request.args.get('room', 'Genel')
    try:
        messages = list(messages_collection.find(
            {'room': room}, 
            {'_id': 0, 'username': 1, 'message': 1, 'timestamp': 1, 'files': 1}
        ).sort('_id', ASCENDING).limit(100))
        
        # Her mesaja profil fotoğrafı bilgisini ekle
        for message in messages:
            user = users_collection.find_one({'username': message['username']})
            if user:
                message['profile_picture'] = user.get('profile_picture', None)
                message['user_id'] = user.get('user_id', None)
            else:
                message['profile_picture'] = None
                message['user_id'] = None
        
        logger.info(f'✅ Oda: {room}, Mesaj sayısı: {len(messages)}')
        return jsonify(messages)
    except Exception as e:
        logger.error(f'❌ Mesaj yükleme hatası: {e}')
        return jsonify([])

@socketio.on('register_user')
def handle_register_user(data):
    username = data.get('username', 'Anonim')
    user_id = data.get('user_id')
    is_admin = data.get('is_admin', False)
    
    # Ban kontrolü
    if is_user_banned(user_id):
        emit('user_banned', {})
        return
    
    active_users[request.sid] = {
        'username': username,
        'user_id': user_id,
        'is_admin': is_admin,
        'socket_id': request.sid
    }
    
    logger.info(f'✅ Kullanıcı kaydedildi - Adı: {username}, ID: {user_id}, Admin: {is_admin}, SID: {request.sid}')
    
    # Çevrimiçi arkadaşlara bildir
    notify_friends_online_status(user_id, True)
    
    emit('user_registered', {'user_id': user_id})
    
    # Tüm kullanıcılara giriş bildirimi gönder
    socketio.emit('user_joined', {'username': username})

@socketio.on('send_message')
def handle_message(data):
    username = data.get('username', 'Anonim')
    message = data.get('message', '')
    room = data.get('room', 'Genel')
    files = data.get('files', [])
    timestamp = datetime.now().strftime('%H:%M')
    
    logger.info(f'📨 Mesaj alındı -> Kullanıcı: {username}, Oda: {room}, Mesaj: {message}, Dosya sayısı: {len(files)}')
    
    # Kullanıcının profil fotoğrafını al
    user = users_collection.find_one({'username': username})
    profile_picture = user.get('profile_picture', None) if user else None
    
    socketio.emit('receive_message', {
        'username': username,
        'message': message,
        'timestamp': timestamp,
        'room': room,
        'files': files,
        'profile_picture': profile_picture
    }, to=room)
    
    logger.info(f'📢 Mesaj {room} odasındaki herkese yayınlandı')
    
    try:
        is_private = '_private_' in room
        is_group = '_group_' in room
        messages_collection.insert_one({
            'username': username,
            'message': message,
            'timestamp': timestamp,
            'room': room,
            'files': files,
            'private': is_private,
            'group': is_group,
            'created_at': datetime.now()
        })
        logger.info(f'💾 Mesaj MongoDB\'ye kaydedildi')
    except Exception as e:
        logger.error(f'❌ MongoDB kayıt hatası: {e}')

@socketio.on('join_room')
def handle_join_room(data):
    room = data.get('room', 'Genel')
    username = data.get('username', 'Anonim')
    join_room(room)
    logger.info(f'✅ {username} (SID: {request.sid}) -> {room} odasına katıldı')
    
    if '_private_' not in room and '_group_' not in room:
        socketio.emit('receive_message', {
            'username': 'Sistem',
            'message': f'{username} odaya katıldı',
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }, to=room)

@socketio.on('leave_room')
def handle_leave_room(data):
    room = data.get('room')
    username = data.get('username', 'Anonim')
    leave_room(room)
    logger.info(f'❌ {username} {room} odasından ayrıldı')

@socketio.on('new_room')
def handle_new_room(data):
    emit('room_created', {'name': data['name']})

@socketio.on('start_private_chat')
def handle_start_private_chat(data):
    from_id = data.get('from_id')
    to_id = data.get('to_id')
    username = data.get('username')
    
    target_user = None
    target_socket_id = None
    
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == to_id:
            target_user = user_data
            target_socket_id = sid
            break
    
    if not target_user:
        emit('error_message', {
            'message': '❌ Kullanıcı çevrimiçi değil veya ID hatalı!'
        })
        logger.info(f'❌ Özel sohbet hatası: Hedef kullanıcı {to_id} bulunamadı')
        return
    
    private_room = f'_private_{sorted([from_id, to_id])[0]}_{sorted([from_id, to_id])[1]}'
    
    logger.info(f'🔒 Özel sohbet başlatılıyor: {username} ({from_id}) <-> {target_user["username"]} ({to_id})')
    logger.info(f'🔒 Oda adı: {private_room}')
    
    socketio.emit('private_room_created', {
        'room': private_room,
        'other_username': target_user['username'],
        'other_id': to_id
    }, to=request.sid)
    
    socketio.emit('private_room_created', {
        'room': private_room,
        'other_username': username,
        'other_id': from_id
    }, to=target_socket_id)
    
    logger.info(f'✅ Özel oda oluşturuldu: {private_room}')

@socketio.on('create_group')
def handle_create_group(data):
    group_name = data.get('group_name', '').strip()
    user1_id = data.get('user1_id', '').strip()
    user2_id = data.get('user2_id', '').strip()
    creator_id = data.get('creator_id')
    creator_username = data.get('creator_username')
    
    # Kullanıcıları kontrol et
    user1 = None
    user2 = None
    user1_socket = None
    user2_socket = None
    
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == user1_id:
            user1 = user_data
            user1_socket = sid
        if user_data.get('user_id') == user2_id:
            user2 = user_data
            user2_socket = sid
    
    if not user1 or not user2:
        emit('group_creation_failed', {
            'message': '❌ Bir veya daha fazla kullanıcı çevrimiçi değil!'
        })
        return
    
    if user1_id == user2_id:
        emit('group_creation_failed', {
            'message': '❌ Aynı kullanıcıyı iki kez ekleyemezsiniz!'
        })
        return
    
    # Grup odası oluştur
    group_room = f'_group_{group_name}_{creator_id}_{user1_id}_{user2_id}'
    
    try:
        # Odayı veritabanına kaydet
        rooms_collection.insert_one({
            'name': group_room,
            'display_name': group_name,
            'type': 'group',
            'members': [creator_id, user1_id, user2_id],
            'created_by': creator_id,
            'created_at': datetime.now()
        })
        
        logger.info(f'👥 Grup oluşturuldu: {group_name} - Üyeler: {creator_username}, {user1["username"]}, {user2["username"]}')
        
        # Tüm kullanıcılara grup odasını bildir
        socketio.emit('group_created', {
            'room': group_room,
            'name': group_name
        }, to=request.sid)
        
        socketio.emit('group_created', {
            'room': group_room,
            'name': group_name
        }, to=user1_socket)
        
        socketio.emit('group_created', {
            'room': group_room,
            'name': group_name
        }, to=user2_socket)
        
    except Exception as e:
        logger.error(f'❌ Grup oluşturma hatası: {e}')
        emit('group_creation_failed', {
            'message': '❌ Grup oluşturulurken bir hata oluştu!'
        })

@socketio.on('send_friend_request')
def handle_send_friend_request(data):
    from_id = data.get('from_id')
    from_username = data.get('from_username')
    to_id = data.get('to_id')
    
    # Kullanıcı var mı kontrol et
    target_user = users_collection.find_one({'user_id': to_id})
    if not target_user:
        emit('error_message', {'message': '❌ Geçersiz kullanıcı ID!'})
        return
    
    # Zaten arkadaş mı kontrol et
    existing_friendship = friendships_collection.find_one({
        '$or': [
            {'user_id': from_id, 'friend_id': to_id},
            {'user_id': to_id, 'friend_id': from_id}
        ]
    })
    if existing_friendship:
        emit('error_message', {'message': '❌ Zaten arkadaşsınız!'})
        return
    
    # Bekleyen istek var mı kontrol et (her iki yönde de)
    existing_request = friend_requests_collection.find_one({
        '$or': [
            {'from_id': from_id, 'to_id': to_id, 'status': 'pending'},
            {'from_id': to_id, 'to_id': from_id, 'status': 'pending'}
        ]
    })
    if existing_request:
        emit('error_message', {'message': '❌ Zaten arkadaşlık isteği gönderdiniz veya size istek gönderilmiş!'})
        return
    
    # Arkadaşlık isteği oluştur
    friend_request = {
        'from_id': from_id,
        'from_username': from_username,
        'to_id': to_id,
        'to_username': target_user['username'],
        'status': 'pending',
        'created_at': datetime.now()
    }
    
    friend_requests_collection.insert_one(friend_request)
    
    logger.info(f'👥 Arkadaşlık isteği: {from_username} -> {target_user["username"]}')
    
    # Hedef kullanıcı çevrimiçi ise bildir
    target_socket_id = None
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == to_id:
            target_socket_id = sid
            break
    
    if target_socket_id:
        socketio.emit('friend_request_received', {
            'from_username': from_username,
            'from_id': from_id
        }, to=target_socket_id)
    
    emit('friend_request_sent', {
        'message': f'✅ Arkadaşlık isteği {target_user["username"]} kullanıcısına gönderildi!'
    })

@socketio.on('accept_friend_request')
def handle_accept_friend_request(data):
    request_id = data.get('request_id')
    from_id = data.get('from_id')
    to_id = data.get('to_id')
    
    # İsteği bul ve güncelle
    friend_request = friend_requests_collection.find_one({'_id': ObjectId(request_id)})
    if not friend_request:
        return
    
    friend_requests_collection.update_one(
        {'_id': ObjectId(request_id)},
        {'$set': {'status': 'accepted', 'responded_at': datetime.now()}}
    )
    
    # Arkadaşlık oluştur
    friendship = {
        'user_id': from_id,
        'friend_id': to_id,
        'created_at': datetime.now()
    }
    
    friendships_collection.insert_one(friendship)
    
    logger.info(f'✅ Arkadaşlık kabul edildi: {friend_request["from_username"]} <-> {friend_request["to_username"]}')
    
    # İstek gönderene bildir
    from_socket_id = None
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == from_id:
            from_socket_id = sid
            break
    
    if from_socket_id:
        socketio.emit('friend_request_accepted', {
            'friend_username': friend_request['to_username'],
            'friend_id': to_id
        }, to=from_socket_id)
    
    # İstek alana bildir
    to_socket_id = None
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == to_id:
            to_socket_id = sid
            break
    
    if to_socket_id:
        socketio.emit('friend_added', {
            'friend_username': friend_request['from_username'],
            'friend_id': from_id
        }, to=to_socket_id)

@socketio.on('reject_friend_request')
def handle_reject_friend_request(data):
    request_id = data.get('request_id')
    from_id = data.get('from_id')
    to_id = data.get('to_id')
    
    # İsteği bul ve güncelle
    friend_request = friend_requests_collection.find_one({'_id': ObjectId(request_id)})
    if not friend_request:
        return
    
    friend_requests_collection.update_one(
        {'_id': ObjectId(request_id)},
        {'$set': {'status': 'rejected', 'responded_at': datetime.now()}}
    )
    
    logger.info(f'❌ Arkadaşlık isteği reddedildi: {friend_request["from_username"]} -> {friend_request["to_username"]}')
    
    # İstek gönderene bildir
    from_socket_id = None
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == from_id:
            from_socket_id = sid
            break
    
    if from_socket_id:
        socketio.emit('friend_request_rejected', {
            'friend_username': friend_request['to_username']
        }, to=from_socket_id)

@socketio.on('delete_room')
def handle_delete_room(data):
    room_name = data.get('room_name')
    user_id = data.get('user_id')
    
    logger.info(f'🔧 Oda silme isteği: {room_name}, Kullanıcı: {user_id}')
    
    # Kullanıcı admin mi kontrol et - aktif kullanıcılardan kontrol et
    user_is_admin = False
    for sid, user_data in active_users.items():
        if user_data.get('user_id') == user_id:
            user_is_admin = user_data.get('is_admin', False)
            break
    
    # Eğer aktif kullanıcılarda bulunamazsa, veritabanından kontrol et
    if not user_is_admin:
        user = users_collection.find_one({'user_id': user_id})
        user_is_admin = user.get('is_admin', False) if user else False
    
    logger.info(f'🔧 Kullanıcı admin mi: {user_is_admin}')
    
    if not user_is_admin:
        emit('room_delete_failed', {'message': '❌ Bu işlem için admin yetkisi gerekiyor!'})
        return
    
    # Sistem odalarını (varsayılan odalar) koru
    default_rooms = ['Genel', 'Teknoloji', 'Spor', 'Müzik', 'Oyun']
    if room_name in default_rooms:
        emit('room_delete_failed', {'message': '❌ Sistem odalarını silemezsiniz!'})
        return
    
    # Özel ve grup odalarını koru
    if '_private_' in room_name or '_group_' in room_name:
        emit('room_delete_failed', {'message': '❌ Özel ve grup odalarını silemezsiniz!'})
        return
    
    # Odayı sil
    result = rooms_collection.delete_one({'name': room_name, 'type': 'public'})
    
    if result.deleted_count > 0:
        # Odadaki mesajları da sil
        messages_collection.delete_many({'room': room_name})
        
        logger.info(f'✅ Admin tarafından oda silindi: {room_name}')
        emit('room_deleted', {'room_name': room_name})
    else:
        emit('room_delete_failed', {'message': '❌ Oda silinemedi veya zaten silinmiş!'})

def notify_friends_online_status(user_id, online):
    """Arkadaşlara çevrimiçi/çevrimdışı durumu bildir"""
    # Kullanıcının arkadaşlarını bul
    friendships = friendships_collection.find({
        '$or': [
            {'user_id': user_id},
            {'friend_id': user_id}
        ]
    })
    
    for friendship in friendships:
        if friendship['user_id'] == user_id:
            friend_id = friendship['friend_id']
        else:
            friend_id = friendship['user_id']
        
        # Arkadaş çevrimiçi ise bildir
        friend_socket_id = None
        for sid, user_data in active_users.items():
            if user_data.get('user_id') == friend_id:
                friend_socket_id = sid
                break
        
        if friend_socket_id:
            socketio.emit('friend_status_changed', {
                'friend_id': user_id,
                'online': online
            }, to=friend_socket_id)

@socketio.on('connect')
def handle_connect():
    user_ip = request.remote_addr
    sid = request.sid
    logger.info(f'✅ Kullanıcı bağlandı - SID: {sid}, IP: {user_ip}')

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in active_users:
        user_info = active_users[sid]
        logger.info(f'❌ Kullanıcı ayrıldı - Adı: {user_info["username"]}, ID: {user_info["user_id"]}, Admin: {user_info.get("is_admin", False)}, SID: {sid}')
        
        # Arkadaşlara çevrimdışı olduğunu bildir
        notify_friends_online_status(user_info.get('user_id'), False)
        
        # Tüm kullanıcılara ayrılma bildirimi gönder
        socketio.emit('user_left', {'username': user_info["username"]})
        
        del active_users[sid]
    else:
        logger.info(f'❌ Kullanıcı ayrıldı - SID: {sid}')

if __name__ == '__main__':
    print('\n' + '='*60)
    print('🚀 GRUP SOHBET SUNUCUSU BAŞLATILDI!')
    print('='*60)
    print('📍 Render\'da çalışıyor...')
    print('='*60)
    print('✨ Özellikler:')
    print('   • ✅ Kullanıcı Kayıt ve Giriş Sistemi')
    print('   • ✅ Güvenli Şifre Hash\'leme (SHA-256)')
    print('   • ✅ Kalıcı Kullanıcı ID\'leri')
    print('   • ✅ Oturum Yönetimi (Flask Session)')
    print('   • ✅ Kullanıcı Profil Sayfası')
    print('   • ✅ 👑 ADMIN SİSTEMİ')
    print('   • ✅ Oda Silme Yetkisi (Admin)')
    print('   • ✅ Kullanıcı Banlama Sistemi')
    print('   • ✅ 3 Kişilik Özel Grup Sistemi')
    print('   • ✅ Özel Sohbet Odaları')
    print('   • ✅ Sadece Grup Üyeleri Grupları Görür')
    print('   • ✅ ARKADAŞLIK SİSTEMİ')
    print('   • ✅ GELEN KUTUSU (Arkadaşlık İstekleri)')
    print('   • ✅ ÇEVRİMİÇİ/ÇEVRİMDIŞI DURUMU')
    print('   • ✅ DOSYA PAYLAŞIM SİSTEMİ')
    print('   • ✅ Resim, Video, Belge Paylaşımı')
    print('   • ✅ 🎤 SES KAYDI ÖZELLİĞİ')
    print('   • ✅ SAĞ PANEL SİSTEMİ (Odalar/Arkadaşlar Listesi)')
    print('   • ✅ SADECE ADMIN TARAFINDAN GÖRÜLEBİLEN KULLANICI LİSTESİ')
    print('   • ✅ PROFİL FOTOĞRAFI YÜKLEME')
    print('   • ✅ ŞİFRE DEĞİŞTİRME ÖZELLİĞİ')
    print('='*60)
    print('🎤 Ses Kaydı Özellikleri:')
    print('   • ✅ Mikrofon erişim izni')
    print('   • ✅ Gerçek zamanlı ses kaydı')
    print('   • ✅ Kayıt süresi göstergesi')
    print('   • ✅ Ses dosyası otomatik yükleme')
    print('   • ✅ Ses mesajı oynatma')
    print('='*60)
    print('👤 Profil Sistemi Özellikleri:')
    print('   • ✅ Profil fotoğrafı yükleme (max 5MB)')
    print('   • ✅ Profil fotoğrafı güncelleme')
    print('   • ✅ Şifre değiştirme')
    print('   • ✅ Profil bilgileri görüntüleme')
    print('='*60 + '\n')

    port = int(os.environ.get("PORT", 5000))
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True
    )
