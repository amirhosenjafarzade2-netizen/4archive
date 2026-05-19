import asyncio
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import aiohttp
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(
    page_title="4chan Archive Crawler",
    layout="wide",
    page_icon="📥"
)

st.title("4chan Archive Crawler")
st.caption("Bulk downloader for 4plebs & warosu archived threads")

# Try to apply nest_asyncio (important for Streamlit)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    st.error("❌ Please install nest_asyncio first:")
    st.code("pip install nest_asyncio")
    st.stop()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

ARCHIVES = {
    "4plebs": "https://archive.4plebs.org",
    "warosu": "https://warosu.org"
}

DATA_DIR = Path("exports")
DATA_DIR.mkdir(exist_ok=True)

# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.header("Crawler Settings")
    
    archive_name = st.selectbox("Archive", list(ARCHIVES.keys()), index=0)
    board = st.text_input("Board", value="biz")
    thread_limit = st.number_input("Max threads to fetch", min_value=1, max_value=5000, value=100)
    
    keyword_filter = st.text_input("Keyword filter (optional)")
    op_only = st.checkbox("Only OP posts", value=False)
    remove_empty = st.checkbox("Remove empty posts", value=True)
    
    concurrency = st.slider("Concurrency (speed)", min_value=1, max_value=30, value=12)
    timeout_seconds = st.slider("Timeout per request (seconds)", 5, 60, 25)
    
    output_formats = st.multiselect(
        "Export formats",
        ["json", "jsonl", "csv", "txt", "sqlite"],
        default=["json", "csv"]
    )

# =====================================================
# HELPER FUNCTIONS
# =====================================================
def normalize_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()

def html_to_text(html):
    soup = BeautifulSoup(html, "lxml")
    return normalize_whitespace(soup.get_text(" ", strip=True))

def extract_thread_ids_4plebs(board_name, limit):
    collected = []
    seen = set()
    page = 1
    
    while len(collected) < limit:
        url = f"https://archive.4plebs.org/{board_name}/page/{page}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
                
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select("a[href*='/thread/']")
            
            for link in links:
                match = re.search(r"/thread/(\d+)", link.get("href", ""))
                if match:
                    tid = match.group(1)
                    if tid not in seen:
                        seen.add(tid)
                        collected.append(tid)
                        if len(collected) >= limit:
                            break
            page += 1
        except:
            break
    return collected[:limit]

def extract_thread_ids_warosu(board_name, limit):
    collected = []
    seen = set()
    url = f"https://warosu.org/{board_name}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return []
            
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a[href*='/thread/']")
        
        for link in links:
            match = re.search(r"/thread/(\d+)", link.get("href", ""))
            if match:
                tid = match.group(1)
                if tid not in seen:
                    seen.add(tid)
                    collected.append(tid)
                    if len(collected) >= limit:
                        break
    except:
        pass
    return collected[:limit]

def build_thread_url(archive, board_name, thread_id):
    if archive == "4plebs":
        return f"https://archive.4plebs.org/{board_name}/thread/{thread_id}"
    return f"https://warosu.org/{board_name}/thread/{thread_id}"

def parse_thread(html, thread_id, board_name, archive_name):
    soup = BeautifulSoup(html, "lxml")
    posts = []
    
    for idx, article in enumerate(soup.select("article.post")):
        blockquote = article.select_one("blockquote")
        if not blockquote:
            continue
            
        content = html_to_text(blockquote.decode_contents())
        
        author = "Anonymous"
        author_el = article.select_one("span.name")
        if author_el:
            author = author_el.get_text(strip=True)
            
        timestamp = ""
        time_el = article.select_one("time")
        if time_el:
            timestamp = time_el.get("datetime", "")
            
        post_id = article.get("id", "").replace("p", "")
        
        posts.append({
            "archive": archive_name,
            "board": board_name,
            "thread_id": thread_id,
            "post_id": post_id,
            "is_op": idx == 0,
            "author": author,
            "timestamp": timestamp,
            "content": content,
            "url": build_thread_url(archive_name, board_name, thread_id)
        })
    return posts

async def fetch_thread(session, semaphore, archive, board_name, thread_id, timeout):
    url = build_thread_url(archive, board_name, thread_id)
    async with semaphore:
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
                return parse_thread(html, thread_id, board_name, archive)
        except:
            return []

async def scrape_threads(archive, board_name, thread_ids, concurrency, timeout):
    semaphore = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = [
            fetch_thread(session, semaphore, archive, board_name, tid, timeout)
            for tid in thread_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
    posts = []
    for result in results:
        if isinstance(result, list):
            posts.extend(result)
    return posts

# =====================================================
# EXPORT FUNCTIONS
# =====================================================
def build_zip(files_dict):
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files_dict.items():
            zf.writestr(filename, content)
    memory_file.seek(0)
    return memory_file

# =====================================================
# MAIN APP
# =====================================================
if st.button("🚀 Start Crawling", type="primary", use_container_width=True):
    start_time = datetime.now()
    
    # Step 1: Get thread IDs
    with st.spinner("Collecting thread IDs..."):
        if archive_name == "4plebs":
            thread_ids = extract_thread_ids_4plebs(board, thread_limit)
        else:
            thread_ids = extract_thread_ids_warosu(board, thread_limit)
    
    st.success(f"✅ Found {len(thread_ids)} threads")

    # Step 2: Scrape threads
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        posts = asyncio.run(
            scrape_threads(
                archive_name, 
                board, 
                thread_ids, 
                concurrency, 
                timeout_seconds
            )
        )
        
        progress_bar.progress(100)
        status_text.success("✅ Scraping completed!")

        # Filtering
        if remove_empty:
            posts = [p for p in posts if p["content"].strip()]
        if keyword_filter:
            kw = keyword_filter.lower()
            posts = [p for p in posts if kw in p["content"].lower()]
        if op_only:
            posts = [p for p in posts if p["is_op"]]

        if not posts:
            st.warning("No posts left after filtering.")
            st.stop()

        df = pd.DataFrame(posts)

        # Statistics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Posts", len(df))
        col2.metric("Threads", df['thread_id'].nunique())
        col3.metric("Unique Authors", df['author'].nunique())
        col4.metric("OP Posts", int(df['is_op'].sum()))

        st.subheader("Preview")
        st.dataframe(df.head(300), use_container_width=True)

        # Export
        export_files = {}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")

        if "json" in output_formats:
            export_files[f"{board}_threads_{timestamp}.json"] = df.to_json(orient="records", indent=2, force_ascii=False)
        if "csv" in output_formats:
            export_files[f"{board}_threads_{timestamp}.csv"] = df.to_csv(index=False)
        if "jsonl" in output_formats:
            export_files[f"{board}_threads_{timestamp}.jsonl"] = "\n".join(json.dumps(p, ensure_ascii=False) for p in posts)
        if "txt" in output_formats:
            txt_lines = [f"[{p['thread_id']}] {p['author']} | {p['timestamp']}\n{p['content']}\n{'─'*80}" for p in posts]
            export_files[f"{board}_threads_{timestamp}.txt"] = "\n".join(txt_lines)
        if "sqlite" in output_formats:
            db_path = DATA_DIR / f"{board}_threads.db"
            df.to_sql("posts", sqlite3.connect(db_path), if_exists="append", index=False)
            export_files[f"{board}_threads_{timestamp}.db"] = db_path.read_bytes()

        if export_files:
            zip_buffer = build_zip(export_files)
            st.download_button(
                label="📥 Download All Files (ZIP)",
                data=zip_buffer,
                file_name=f"{board}_archive_{timestamp}.zip",
                mime="application/zip",
                use_container_width=True
            )

        elapsed = datetime.now() - start_time
        st.success(f"✅ Finished in {elapsed}")

    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)

st.divider()
st.caption("For research and educational use only • Respect archive rules")
