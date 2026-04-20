import requests
import base64
import hashlib
import re
import io
import time
import json
import threading
import sys
import os
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from PIL import Image
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes

# --- CONFIGURATION ---
PORT = 3000
ENCRYPTION_KEY = "nic@impds#dedup05613"
USERNAME = "adminWB"
PASSWORD = "2p3MrgdgV8s9"

# Check OCR availability
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[-] Warning: 'pytesseract' library not found.")

app = Flask(__name__)

# --- ENCRYPTION HELPER ---
class CryptoHandler:
    def __init__(self, passphrase):
        self.passphrase = passphrase.encode('utf-8')

    def _derive_key_and_iv(self, salt, key_length=32, iv_length=16):
        d = d_i = b''
        while len(d) < key_length + iv_length:
            d_i = hashlib.md5(d_i + self.passphrase + salt).digest()
            d += d_i
        return d[:key_length], d[key_length:key_length+iv_length]

    def encrypt(self, plain_text):
        salt = get_random_bytes(8)
        key, iv = self._derive_key_and_iv(salt)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted_bytes = cipher.encrypt(pad(plain_text.encode('utf-8'), AES.block_size))
        return base64.b64encode(b"Salted__" + salt + encrypted_bytes).decode('utf-8')

    def decrypt(self, encrypted_b64):
        try:
            encrypted_data = base64.b64decode(encrypted_b64)
            if encrypted_data[:8] != b'Salted__':
                return None
            salt = encrypted_data[8:16]
            cipher_bytes = encrypted_data[16:]
            key, iv = self._derive_key_and_iv(salt)
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted_bytes = unpad(cipher.decrypt(cipher_bytes), AES.block_size)
            return decrypted_bytes.decode('utf-8')
        except Exception:
            return None

crypto_engine = CryptoHandler(ENCRYPTION_KEY)

# --- AUTOMATION & BOT LOGIC ---
class IMPDSBot:
    def __init__(self):
        self.init_session()
        self.lock = threading.Lock()
        self.jsessionid = None
        self.last_login_time = 0
        self.user_salt = None
        self.csrf_token = None
        self.base_url = "https://impds.nic.in/impdsdeduplication"

    def init_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://impds.nic.in',
            'Referer': 'https://impds.nic.in/impdsdeduplication/LoginPage',
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Linux"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'priority': 'u=1, i'
        })

    def sha512(self, text):
        return hashlib.sha512(text.encode('utf-8')).hexdigest()

    def ensure_session(self):
        with self.lock:
            if self.jsessionid and (time.time() - self.last_login_time < 1200):
                return True

            print("\n🔄 Session expired or missing. Starting Login Sequence...")
            max_retries = 5
            for attempt in range(1, max_retries + 1):
                print(f"🔹 Login Attempt {attempt}/{max_retries}...")
                if attempt > 1:
                    print("🧹 Cleaning session for retry...")
                    self.init_session()
                    time.sleep(2)

                if self.perform_login():
                    return True
                else:
                    if attempt < max_retries:
                        print("⚠️ Retrying in 2 seconds...")
                        time.sleep(2)
            return False

    def perform_login(self):
        try:
            page_headers = self.session.headers.copy()
            if 'X-Requested-With' in page_headers: del page_headers['X-Requested-With']
            page_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            
            r = self.session.get(f"{self.base_url}/LoginPage", headers=page_headers, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            csrf_input = soup.find('input', {'name': 'REQ_CSRF_TOKEN'})
            self.csrf_token = csrf_input.get('value') if csrf_input else None
            
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'USER_SALT' in script.string:
                    match = re.search(r"USER_SALT\s*=\s*['\"]([^'\"]+)['\"]", script.string)
                    if match: self.user_salt = match.group(1)

            if not self.csrf_token or not self.user_salt:
                return False

            c_res = self.session.post(f"{self.base_url}/ReloadCaptcha", timeout=10)
            captcha_text = self.solve_captcha(c_res.json().get('captchaBase64'))
            if not captcha_text: return False

            salted_pass = self.sha512(self.sha512(self.user_salt) + self.sha512(PASSWORD))
            payload = {'userName': USERNAME, 'password': salted_pass, 'captcha': captcha_text, 'REQ_CSRF_TOKEN': self.csrf_token}
            
            l_res = self.session.post(f"{self.base_url}/UserLogin", data=payload, timeout=20)
            
            if l_res.status_code == 200:
                if "Welcome" in l_res.text or "Dashboard" in l_res.text or (l_res.headers.get('content-type') == 'application/json' and not l_res.json().get('athenticationError')):
                    self.jsessionid = self.session.cookies.get('JSESSIONID')
                    self.last_login_time = time.time()
                    print(f"✅ Login Successful! JSESSIONID: {self.jsessionid[:10]}...")
                    return True
            return False
        except Exception as e:
            print(f"[-] Login Exception: {e}")
            return False

    def solve_captcha(self, b64_str):
        if not b64_str or not OCR_AVAILABLE: return None
        try:
            image = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('L')
            image = image.point(lambda x: 0 if x < 145 else 255, '1')
            text = pytesseract.image_to_string(image, config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
            clean = ''.join(filter(str.isalnum, text.strip().upper()))
            if len(clean) >= 4:
                print(f"[?] OCR Guessed: {clean}")
                return clean
            return None
        except: return None

    # --- STEP 1: Search Aadhaar to get RC Number ---
    def get_rc_id_from_aadhaar(self, encrypted_aadhaar):
        if not self.ensure_session(): return None
        headers = self.session.headers.copy()
        headers['Referer'] = f"{self.base_url}/search"
        data = {'search': 'A', 'aadhar': encrypted_aadhaar}
        try:
            res = self.session.post(f"{self.base_url}/search", data=data, headers=headers, timeout=30)
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='table-striped')
            if table:
                rows = table.find('tbody').find_all('tr')
                if rows:
                    cols = rows[0].find_all('td')
                    if len(cols) >= 4:
                        return cols[3].get_text(strip=True)
            return None
        except: return None

    # --- STEP 2: Fetch Advanced Details from RC Report ---
    def get_advanced_report(self, rc_id):
        if not self.ensure_session(): return {"error": "Auth failed"}
        headers = self.session.headers.copy()
        headers['Referer'] = f"{self.base_url}/RcSummaryPage"
        data = {'rationcardId': rc_id}
        try:
            print(f"[*] Fetching Full Report for RC: {rc_id}")
            res = self.session.post(f"{self.base_url}/RcReportSummary", data=data, headers=headers, timeout=30)
            return self.parse_advanced_html(res.text)
        except Exception as e:
            return {"error": str(e)}

    def parse_advanced_html(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        data = {"card_info": {}, "members": [], "monthly_summary": []}
        tables = soup.find_all('table')
        if not tables: return {"error": "Report data empty"}

        # 1. Card Summary Info
        info_cells = tables[0].find_all('td')
        for cell in info_cells:
            text = cell.get_text(strip=True)
            if ":" in text:
                k, v = text.split(":", 1)
                data["card_info"][k.strip()] = v.strip()

        # 2. Detailed Member List (Relationship, eKYC, UID)
        for table in tables:
            th_text = table.get_text().lower()
            if "relationship" in th_text and "member name" in th_text:
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 6:
                        data["members"].append({
                            "member_id": cols[0].get_text(strip=True),
                            "member_name": cols[1].get_text(strip=True),
                            "gender": cols[2].get_text(strip=True),
                            "uid_masked": cols[3].get_text(strip=True),
                            "relationship": cols[4].get_text(strip=True),
                            "ekyc_status": cols[5].get_text(strip=True),
                            "cr_last_updated": cols[6].get_text(strip=True) if len(cols) > 6 else ""
                        })
                break

        # 3. Last 4 Months Summary
        for table in tables:
            if "data captured on" in table.get_text().lower():
                rows = table.find('tbody').find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 4:
                        data["monthly_summary"].append({
                            "month": cols[0].get_text(strip=True),
                            "captured_on": cols[1].get_text(strip=True),
                            "member_count": cols[3].get_text(strip=True)
                        })
                break
        return data

bot = IMPDSBot()

@app.route('/full-search', methods=['GET'])
def api_full_search():
    aadhaar = request.args.get('aadhaar')
    if not aadhaar: return jsonify({"success": False, "error": "Missing aadhaar"}), 400

    print(f"\n--- Processing New Search: {aadhaar} ---")
    
    # Encryption
    decrypted_check = crypto_engine.decrypt(aadhaar)
    encrypted_val = aadhaar if decrypted_check else crypto_engine.encrypt(aadhaar)

    # Step 1: Get RC ID
    rc_id = bot.get_rc_id_from_aadhaar(encrypted_val)
    if not rc_id:
        return jsonify({"success": False, "error": "No Ration Card found for this Aadhaar"}), 404

    # Step 2: Get Advanced details
    details = bot.get_advanced_report(rc_id)
    
    return jsonify({
        "success": True, 
        "ration_card_id": rc_id,
        "details": details
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"success": True, "session": bool(bot.jsessionid)})

if __name__ == "__main__":
    print("🚀 Initializing IMPDS Advanced Bot...")
    bot.ensure_session()
    app.run(host='0.0.0.0', port=PORT, debug=False)