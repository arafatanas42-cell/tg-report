import os
import json
import random
import time
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock, Event
from collections import deque
import requests
from bs4 import BeautifulSoup
import phonenumbers
from phonenumbers import PhoneNumberFormat
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem

try:
    from emailtools import generate
except ImportError:
    def generate(_domain="gmail"):
        return f"user{random.randint(10000, 999999)}@gmail.com"

# =========================================================
# Directory Setup & Config
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THREAD_LIMIT = 100 # Render Free Tier-এ ১০০ থ্রেড ক্র্যাশ করবে না

class TelegramState:
    def __init__(self):
        self.lock = Lock()
        self.is_running = False
        self.stop_event = Event()
        self.target = ""
        self.success_count = 0
        self.error_count = 0
        self.start_time = 0
        self.logs = deque(maxlen=300)
        self.log_seq = 0

    def log(self, text):
        if not text.startswith('SUCCESS'): return
        with self.lock:
            self.log_seq += 1
            self.logs.append({"id": self.log_seq, "timestamp": time.time(), "log": text})

    def get_stats(self, since_id=0):
        with self.lock:
            return {
                "running": self.is_running,
                "target": self.target,
                "success_count": self.success_count,
                "error_count": self.error_count,
                "uptime_seconds": int(time.time() - self.start_time) if self.start_time else 0,
                "log_seq": self.log_seq,
                "recent_logs": [l for l in self.logs if l["id"] > since_id]
            }

    def inc_success(self):
        with self.lock: self.success_count += 1

    def inc_error(self):
        with self.lock: self.error_count += 1

    def reset_for_start(self, target):
        with self.lock:
            if self.is_running: return False
            self.is_running = True
            self.stop_event.clear()
            self.target = target
            self.start_time = time.time()
            self.success_count = 0
            self.error_count = 0
            self.logs.clear()
            return True

    def request_stop(self):
        with self.lock:
            if not self.is_running: return False
            self.is_running = False
            self.stop_event.set()
            return True

state = TelegramState()

# =========================================================
# Headers & User Agents
# =========================================================
software_names = [SoftwareName.CHROME.value, SoftwareName.FIREFOX.value, SoftwareName.EDGE.value, SoftwareName.OPERA.value]
operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value, OperatingSystem.MAC.value]
user_agent_rotator = UserAgent(software_names=software_names, operating_systems=operating_systems, limit=1200)

BASE_HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'max-age=0',
    'content-type': 'application/x-www-form-urlencoded',
    'origin': 'https://telegram.org',
    'referer': 'https://telegram.org/support',
    'sec-ch-ua': '"Chromium";v="120", "Not(A:Brand";v="24", "Google Chrome";v="120"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1'
}
TIME_OUT = 15
PROXIES_TYPES = ('http', 'socks4', 'socks5')

# =========================================================
# Core Functions
# =========================================================
def load_proxies(proxy_type):
    filename = os.path.join(BASE_DIR, f"{proxy_type}_proxies.txt")
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [p.strip() for p in f.readlines() if p.strip()]
    except:
        return []

def extract_csrf_token(html):
    soup = BeautifulSoup(html, 'html.parser')
    token_input = soup.find('input', {'name': 'csrf_token'})
    return token_input.get('value', '') if token_input else None

def generate_phone():
    while True:
        cc = "+{}".format(random.randint(1, 99))
        nn = str(random.randint(1000000000, 9999999999))
        try:
            p = phonenumbers.parse(cc + nn)
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, PhoneNumberFormat.E164)
        except:
            continue

def send_report(proxy, proxy_type, username):
    if not state.is_running or state.stop_event.is_set():
        return

    USER_AGENT = user_agent_rotator.get_random_user_agent()
    headers = BASE_HEADERS.copy()
    headers['user-agent'] = USER_AGENT
    url = 'https://telegram.org/support'
    
    try:
        session = requests.Session()
        proxy_dict = {'http': f'{proxy_type}://{proxy}', 'https': f'{proxy_type}://{proxy}'}
        
        # 1. GET Request (CSRF + Form)
        response = session.get(url, proxies=proxy_dict, timeout=TIME_OUT, headers=headers)
        if response.status_code != 200: 
            state.inc_error()
            return
        
        csrf_token = extract_csrf_token(response.text)
        soup = BeautifulSoup(response.text, 'html.parser')
        form = soup.find('form', action="/support")
        if not form: 
            state.inc_error()
            return

        # 2. Message Generation
        try:
            msg_file = os.path.join(BASE_DIR, 'message.txt')
            with open(msg_file, 'r', encoding='utf-8') as f:
                message = random.choice(f.readlines()).strip().replace('{username}', username)
        except:
            message = f"Please investigate {username}"

        email = generate('gmail')
        phone = generate_phone()

        # 3. Build Form Data
        data = {}
        for input_tag in form.find_all(['input', 'textarea']):
            name_attr = input_tag.get('name')
            if not name_attr: continue
            if name_attr == 'csrf_token': data[name_attr] = csrf_token
            elif 'email' in name_attr.lower(): data[name_attr] = email
            elif 'phone' in name_attr.lower(): data[name_attr] = phone
            elif 'problem' in name_attr.lower() or 'message' in name_attr.lower(): data[name_attr] = message
            else: data[name_attr] = input_tag.get('value', '')

        textarea = form.find('textarea')
        if textarea and textarea.get('name'):
            data[textarea.get('name')] = message

        # 4. POST Request
        post_headers = headers.copy()
        post_headers.pop('content-length', None)
        post_headers['cookie'] = '; '.join([f"{k}={v}" for k, v in session.cookies.items()])
        
        response = session.post(url, data=data, headers=post_headers, timeout=TIME_OUT)
        
        if response.status_code == 200:
            state.inc_success()
            state.log(f"SUCCESS: {email} | Proxy: {proxy}")
        else:
            state.inc_error()
            
    except Exception:
        state.inc_error()

def get_views_from_proxies(proxy_type, proxies, username):
    for proxy in proxies:
        if not state.is_running or state.stop_event.is_set():
            break
        send_report(proxy, proxy_type, username)
        # Render সার্ভার যাতে ওভারলোড না হয়, তাই ছোট ডিলে
        time.sleep(random.uniform(0.1, 0.3))

def worker_loop(target_username):
    state.log(f"SUCCESS: Worker initiated for target: {target_username}")

    while state.is_running and not state.stop_event.is_set():
        threads = []
        for proxy_type in PROXIES_TYPES:
            if state.stop_event.is_set(): break

            proxies = load_proxies(proxy_type)
            if not proxies: continue

            # Chunking (প্রতি ব্যাচে ৫০টি থ্রেড)
            chunked = [proxies[i:i + 50] for i in range(0, len(proxies), 50)]
            for chunk in chunked:
                if state.stop_event.is_set(): break
                t = Thread(target=get_views_from_proxies, args=(proxy_type, chunk, target_username), daemon=True)
                threads.append(t)
                t.start()

        for t in threads:
            while t.is_alive():
                if state.stop_event.is_set(): break
                t.join(timeout=0.5)

        if state.stop_event.is_set(): break
        time.sleep(2)

# =========================================================
# HTTP Server
# =========================================================
class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code, html):
        body = html.encode('utf-8')
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args): pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        command = params.get("command", [""])[0].lower()
        raw_link = params.get("link", [""])[0].strip()
        
        username = raw_link.split('t.me/')[-1].split('/')[0].lstrip('@') if 't.me/' in raw_link else raw_link.lstrip('@')

        if command == "/start":
            if not username:
                return self._send_json(400, {"status": "error", "message": "Missing link"})
            if not state.reset_for_start(username):
                return self._send_json(409, {"status": "error", "message": f"Task already running for {state.target}"})
            
            Thread(target=worker_loop, args=(username,), daemon=True).start()
            return self._send_json(200, {"status": "success", "message": f"Started for {username}"})

        elif command == "/stop":
            if not state.request_stop():
                return self._send_json(200, {"status": "idle", "message": "Not running"})
            return self._send_json(200, {"status": "success", "message": "Stopping"})

        elif command == "/logs":
            if params.get("stream", ["0"])[0] == "1":
                return self._stream_logs()
            since_id = int(params.get("since_id", ["0"])[0])
            return self._send_json(200, {"status": "success", "data": state.get_stats(since_id)})
        
        elif command == "" and parsed.path in ("/", "/index.html", "/dashboard"):
            html_path = os.path.join(BASE_DIR, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                return self._send_html(200, html_content)
            else:
                return self._send_html(404, "<h1>index.html not found!</h1>")

        return self._send_json(400, {"status": "error", "message": "Invalid Command"})

    def _stream_logs(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        
        last_seq = 0
        try:
            while True:
                stats = state.get_stats(since_id=last_seq)
                self.wfile.write(f"data: {json.dumps(stats)}\n\n".encode('utf-8'))
                self.wfile.flush()
                if stats["recent_logs"]:
                    last_seq = stats["recent_logs"][-1]["id"]
                if not stats["running"] and stats["log_seq"] == last_seq: break
                time.sleep(1)
        except:
            pass

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"🚀 Nexus Server running on port {PORT}")
    server.serve_forever()
