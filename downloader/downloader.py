import os
import sys
import time
import sqlite3
import hashlib
import requests
import shutil
import re
from urllib.parse import urlparse, parse_qs
import tempfile
import uuid
import base64

DB_PATH = '/data/downloads.db'
LINKS_FILE = '/app_root/links.txt'
DOWNLOADS_DIR = '/downloads'

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS links (
            url TEXT PRIMARY KEY,
            status TEXT,
            filename TEXT,
            added_at REAL,
            retries INTEGER DEFAULT 0
        )
    ''')
    try:
        c.execute('ALTER TABLE links ADD COLUMN retries INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass # Column already exists
    
    # Reset any stuck downloading files back to pending on startup
    c.execute("UPDATE links SET status = 'pending' WHERE status = 'downloading'")
    
    conn.commit()
    conn.close()

def get_file_hash(filepath):
    if not os.path.exists(filepath):
        return None
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        buf = f.read(65536)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(65536)
    return hasher.hexdigest()

def extract_md5_from_url(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if 'md5' in qs:
        val = qs['md5'][0]
        # 1. 32-character hex MD5
        if len(val) == 32 and all(c in '0123456789abcdefABCDEF' for c in val):
            return val.lower()
        # 2. 22 or 24 character base64url MD5
        if len(val) in (22, 24):
            try:
                clean_val = val.rstrip('=')
                pad = '=' * (4 - len(clean_val) % 4)
                decoded = base64.urlsafe_b64decode(clean_val + pad)
                return decoded.hex().lower()
            except Exception:
                pass
        return val.lower()
    return None

def extract_filename(response, url):
    if 'Content-Disposition' in response.headers:
        cd = response.headers.get('Content-Disposition')
        match = re.search(r'filename="?([^"]+)"?', cd)
        if match:
            return match.group(1)
    
    parsed = urlparse(url)
    path = parsed.path
    if path:
        name = os.path.basename(path)
        if name:
            return name
    return "downloaded_file"

def update_links_txt_status(url, new_status):
    if not os.path.exists(LINKS_FILE):
        return
    
    try:
        original_hash = get_file_hash(LINKS_FILE)
        
        with open(LINKS_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        changed = False
        for line in lines:
            line_strip = line.strip()
            if (line_strip == url or (line_strip.startswith('http') and url in line_strip)) and not line_strip.startswith(f"[{new_status}]"):
                nl = '\r\n' if line.endswith('\r\n') else '\n'
                new_lines.append(f"[{new_status}] {line_strip}{nl}")
                changed = True
            else:
                new_lines.append(line)
                
        if changed:
            temp_dir = os.path.dirname(LINKS_FILE)
            with tempfile.NamedTemporaryFile('w', dir=temp_dir, delete=False, encoding='utf-8') as tempf:
                temp_path = tempf.name
                tempf.writelines(new_lines)
                
            current_hash = get_file_hash(LINKS_FILE)
            if current_hash == original_hash:
                os.replace(temp_path, LINKS_FILE)
                try:
                    os.chmod(LINKS_FILE, 0o666)
                except Exception:
                    pass
            else:
                os.remove(temp_path)
    except Exception as e:
        print(f"Error updating links.txt status for {url} to {new_status}: {e}", flush=True)

def sync_links():
    if not os.path.exists(LINKS_FILE):
        print(f"Links file {LINKS_FILE} not found.", flush=True)
        return
    
    print(f"Scanning {LINKS_FILE} for changes...", flush=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. Read links.txt
    with open(LINKS_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for line in lines:
        url = line.strip()
        if url.startswith('http://') or url.startswith('https://'):
            c.execute('SELECT status FROM links WHERE url = ?', (url,))
            row = c.fetchone()
            if not row:
                c.execute('INSERT INTO links (url, status, added_at) VALUES (?, ?, ?)', (url, 'pending', time.time()))
    
    # 2. Check for missing completed files to remove from links.txt
    c.execute("SELECT url, filename FROM links WHERE status = 'completed'")
    completed_links = c.fetchall()
    
    urls_to_remove = set()
    for url, filename in completed_links:
        if filename:
            file_path = os.path.join(DOWNLOADS_DIR, filename)
            if not os.path.exists(file_path):
                # File was moved/deleted by user
                urls_to_remove.add(url)
                
    if urls_to_remove:
        # Also update checks_md5.txt if it exists (same lifecycle)
        checks_file = os.path.join(DOWNLOADS_DIR, 'checks_md5.txt')
        if os.path.exists(checks_file):
            try:
                with open(checks_file, 'r', encoding='utf-8') as f:
                    checks_lines = f.readlines()
                    
                new_checks_lines = []
                checks_changed = False
                for line in checks_lines:
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        status_part, line_url = parts
                        if line_url in urls_to_remove and not status_part.startswith('[END]'):
                            new_checks_lines.append(f"[END] {line}")
                            checks_changed = True
                        else:
                            new_checks_lines.append(line)
                    else:
                        new_checks_lines.append(line)
                        
                if checks_changed:
                    with tempfile.NamedTemporaryFile('w', dir=DOWNLOADS_DIR, delete=False, encoding='utf-8') as tempf:
                        temp_path = tempf.name
                        tempf.writelines(new_checks_lines)
                    os.replace(temp_path, checks_file)
                    try:
                        os.chmod(checks_file, 0o666)
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error updating checks_md5.txt: {e}", flush=True)

        # Atomic update logic
        original_hash = get_file_hash(LINKS_FILE)
        
        # Filter lines
        new_lines = []
        for line in lines:
            line_strip = line.strip()
            matched_url = None
            for u in urls_to_remove:
                if u in line_strip:
                    matched_url = u
                    break
            
            if matched_url and not line_strip.startswith('[END]'):
                new_lines.append(f"[END] {line}")
            else:
                new_lines.append(line)
                
        # Write to temp file
        temp_dir = os.path.dirname(LINKS_FILE)
        with tempfile.NamedTemporaryFile('w', dir=temp_dir, delete=False, encoding='utf-8') as tempf:
            temp_path = tempf.name
            tempf.writelines(new_lines)
            
        # Check original hash again
        current_hash = get_file_hash(LINKS_FILE)
        if current_hash == original_hash:
            # Safe to overwrite
            os.replace(temp_path, LINKS_FILE)
            try:
                os.chmod(LINKS_FILE, 0o666)
            except Exception:
                pass
            # Remove from DB entirely so we don't track it anymore
            for url in urls_to_remove:
                c.execute('DELETE FROM links WHERE url = ?', (url,))
        else:
            # File was modified concurrently, abort
            os.remove(temp_path)
            
    conn.commit()
    conn.close()

def check_disk_space(required_bytes=None):
    # Free space must be at least 5GB (5*1024^3 bytes) OR required_bytes + 1GB
    total, used, free = shutil.disk_usage(DOWNLOADS_DIR)
    gb = 1024 * 1024 * 1024
    if free >= 5 * gb:
        return True
    if required_bytes is not None and free >= (required_bytes + 1 * gb):
        return True
    return False

def process_downloads():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url, retries FROM links WHERE status = 'pending' ORDER BY added_at ASC LIMIT 1")
    row = c.fetchone()
    
    if not row:
        conn.close()
        return False, False # No pending downloads
        
    url = row[0]
    retries = row[1] if row[1] is not None else 0
    md5_status = '[NONE]'
    
    print(f"Starting download for {url}", flush=True)
    c.execute("UPDATE links SET status = 'downloading' WHERE url = ?", (url,))
    conn.commit()
    
    tmp_dir = os.path.join(DOWNLOADS_DIR, f".tmp_dl_{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    
    success = False
    filename = None
    try:
        # Head request to get size and name
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            content_length = r.headers.get('Content-Length')
            if content_length:
                content_length = int(content_length)
            else:
                content_length = None
                
            if not check_disk_space(content_length):
                print(f"Not enough disk space for {url}. Skipping.", flush=True)
                # Revert to pending to try again later
                c.execute("UPDATE links SET status = 'pending' WHERE url = ?", (url,))
                conn.commit()
                return True, False
                
            filename = os.path.basename(extract_filename(r, url))
            filepath = os.path.join(tmp_dir, filename)
            
            # Download file
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
        # Verify MD5 if present
        expected_md5 = extract_md5_from_url(url)
        if expected_md5:
            actual_md5 = get_file_hash(filepath)
            if actual_md5 and actual_md5.lower() == expected_md5.lower():
                md5_status = '[MATCH]'
                print(f"MD5 match for {url}", flush=True)
            else:
                md5_status = '[MISMATCH]'
                print(f"MD5 mismatch for {url}: expected {expected_md5}, got {actual_md5} (optional)", flush=True)
                
        # Move file to final destination
        final_filepath = os.path.join(DOWNLOADS_DIR, filename)
        shutil.move(filepath, final_filepath)
        success = True
        print(f"Successfully downloaded {filename}", flush=True)
        
    except Exception as e:
        print(f"Download failed for {url}: {e}", flush=True)
    finally:
        # Cleanup tmp dir
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            
        if success:
            c.execute("UPDATE links SET status = 'completed', filename = ? WHERE url = ?", (filename, url))
            update_links_txt_status(url, 'COMPLETED')
            
            # Write MD5 check status to checks_md5.txt
            checks_file = os.path.join(DOWNLOADS_DIR, 'checks_md5.txt')
            try:
                existed = os.path.exists(checks_file)
                with open(checks_file, 'a', encoding='utf-8') as f:
                    f.write(f"{md5_status} {url}\n")
                if not existed:
                    try:
                        os.chmod(checks_file, 0o666)
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error writing to checks_md5.txt: {e}", flush=True)
        else:
            retries += 1
            if retries >= 100:
                print(f"Download for {url} failed 100 times. Marking as failed.", flush=True)
                c.execute("UPDATE links SET status = 'failed', retries = ? WHERE url = ?", (retries, url))
                update_links_txt_status(url, 'FAILED')
            else:
                print(f"Download for {url} failed. Retrying (attempt {retries}/100)...", flush=True)
                c.execute("UPDATE links SET status = 'pending', retries = ? WHERE url = ?", (retries, url))
        conn.commit()
        conn.close()
        
    return True, success

def main():
    print("Starting multi-link downloader service...", flush=True)
    init_db()
    
    last_sync_time = 0
    last_mtime = 0
    last_size = 0
    SYNC_INTERVAL = 300 # 5 minutes
    
    while True:
        current_time = time.time()
        
        # Check if links.txt has changed on disk
        file_changed = False
        try:
            if os.path.exists(LINKS_FILE):
                mtime = os.path.getmtime(LINKS_FILE)
                size = os.path.getsize(LINKS_FILE)
                if mtime != last_mtime or size != last_size:
                    file_changed = True
                    last_mtime = mtime
                    last_size = size
        except Exception as e:
            print(f"Error checking links.txt mtime: {e}", flush=True)
            
        # Sync if file changed or interval elapsed
        if file_changed or (current_time - last_sync_time >= SYNC_INTERVAL):
            try:
                sync_links()
                last_sync_time = time.time()
            except Exception as e:
                print(f"Error syncing links: {e}", flush=True)
                
        # Process one download
        try:
            processed, success = process_downloads()
            if not processed:
                # If no pending downloads, sleep before checking again
                time.sleep(10)
            elif not success:
                # If the download failed, sleep to avoid rapid retry loops
                time.sleep(10)
        except Exception as e:
            print(f"Error processing downloads: {e}", flush=True)
            time.sleep(10)

if __name__ == '__main__':
    main()
