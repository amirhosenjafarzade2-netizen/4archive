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
st.set_page_config(page_title="4chan Archive Crawler", layout="wide")
st.title("4chan Archive Crawler")
st.caption("Bulk downloader for archived 4chan threads")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
ARCHIVES = {"4plebs": "https://archive.4plebs.org", "warosu": "https://warosu.org"}
DATA_DIR = Path("exports")
DATA_DIR.mkdir(exist_ok=True)

# Fix for asyncio in Streamlit
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    st.warning("Install nest_asyncio for better stability: `pip install nest_asyncio`")

# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.header("Crawler Settings")
    archive_name = st.selectbox("Archive", list(ARCHIVES.keys()))
    board = st.text_input("Board", value="biz")
    thread_limit = st.number_input("Threads to fetch", 1, 10000, 100)
    keyword_filter = st.text_input("Keyword filter")
    op_only = st.checkbox("Only OP posts")
    remove_empty = st.checkbox("Remove empty posts", value=True)
    concurrency = st.slider("Concurrency", 1, 30, 10)
    timeout_seconds = st.slider("Request timeout", 5, 120, 30)
    output_formats = st.multiselect(
        "Export formats", ["json", "jsonl", "csv", "txt", "sqlite"], default=["json"]
    )

# =====================================================
# HELPERS
# =====================================================
def normalize_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()

def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return normalize_whitespace(soup.get_text(" ", strip=True))

def extract_thread_ids_4plebs(board_name, limit):
    collected, seen = [], set()
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
                if match and (tid := match.group(1)) not in seen:
                    seen.add(tid)
                    collected.append(tid)
                    if len(collected) >= limit:
                        break
            page += 1
        except:
            break
    return collected[:limit]

def extract_thread_ids_warosu(board_name, limit):
    # ... similar logic (you can keep or improve)
    collected, seen = [], set()
    url = f"https://warosu.org/{board_name}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.select("a[href*='/thread/']"):
            match = re.search(r"/thread/(\d+)", link.get("href", ""))
            if match and (tid := match.group(1)) not in seen:
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
    for index, article in enumerate(soup.select("article.post")):
        blockquote = article.select_one("blockquote")
        if not blockquote:
            continue
        content = html_to_text(blockquote.decode_contents())
        posts.append({
            "archive": archive_name,
            "board": board_name,
            "thread_id": thread_id,
            "post_id": article.get("id", ""),
            "is_op": index == 0,
            "author": article.select_one("span.name") or "Anonymous",
            "timestamp": article.select_one("time").get("datetime", "") if article.select_one("time") else "",
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
        tasks = [fetch_thread(session, semaphore, archive, board_name, tid, timeout) for tid in thread_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    posts = []
    for result in results:
        if isinstance(result, list):
            posts.extend(result)
    return posts

# =====================================================
# EXPORTS
# =====================================================
def build_zip(files_dict):
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files_dict.items():
            zf.writestr(name, content)
    memory_file.seek(0)
    return memory_file

# =====================================================
# MAIN
# =====================================================
if st.button("🚀 Start Crawl", type="primary"):
    start_time = datetime.utcnow()
    
    with st.spinner("Collecting thread IDs..."):
        if archive_name == "4plebs":
            thread_ids = extract_thread_ids_4plebs(board, thread_limit)
        else:
            thread_ids = extract_thread_ids_warosu(board, thread_limit)
    
    st.success(f"Found {len(thread_ids)} threads")

    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        # Run async scrape
        posts = asyncio.run(
            scrape_threads(archive_name, board, thread_ids, concurrency, timeout_seconds)
        )
        
        progress_bar.progress(100)
        status_text.success("Scraping completed!")

        # Post-processing
        if remove_empty:
            posts = [p for p in posts if p["content"].strip()]
        if keyword_filter:
            posts = [p for p in posts if keyword_filter.lower() in p["content"].lower()]
        if op_only:
            posts = [p for p in posts if p["is_op"]]

        if not posts:
            st.warning("No posts matched your filters.")
            st.stop()

        df = pd.DataFrame(posts)

        # Stats
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Posts", len(df))
        col2.metric("Threads", df['thread_id'].nunique())
        col3.metric("Authors", df['author'].nunique())
        col4.metric("OPs", int(df['is_op'].sum()))

        st.subheader("Preview")
        st.dataframe(df.head(500), use_container_width=True)

        # Exports
        export_files = {}
        if "json" in output_formats:
            export_files["threads.json"] = df.to_json(orient="records", indent=2, force_ascii=False)
        if "csv" in output_formats:
            export_files["threads.csv"] = df.to_csv(index=False)
        if "jsonl" in output_formats:
            export_files["threads.jsonl"] = "\n".join(json.dumps(p, ensure_ascii=False) for p in posts)
        if "txt" in output_formats:
            txt = [f"[{p['thread_id']}] {p['author']}\n{p['content']}\n{'-'*80}" for p in posts]
            export_files["threads.txt"] = "\n".join(txt)
        if "sqlite" in output_formats:
            db_path = DATA_DIR / "threads.db"
            df.to_sql("posts", sqlite3.connect(db_path), if_exists="append", index=False)
            export_files["threads.db"] = db_path.read_bytes()

        if export_files:
            zip_buffer = build_zip(export_files)
            st.download_button(
                "📥 Download Export ZIP",
                data=zip_buffer,
                file_name=f"{board}_archive_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                mime="application/zip"
            )

        elapsed = datetime.utcnow() - start_time
        st.caption(f"✅ Finished in {elapsed}")

    except Exception as e:
        st.error(f"Error during scraping: {e}")
        st.exception(e)
