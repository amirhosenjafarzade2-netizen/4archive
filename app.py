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
# PAGE
# =====================================================

st.set_page_config(
    page_title="4chan Archive Crawler",
    layout="wide"
)

st.title("4chan Archive Crawler")
st.caption("Bulk downloader for archived 4chan threads")


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    st.header("Crawler Settings")

    archive_name = st.selectbox(
        "Archive",
        list(ARCHIVES.keys())
    )

    board = st.text_input(
        "Board",
        value="biz"
    )

    thread_limit = st.number_input(
        "Threads to fetch",
        min_value=1,
        max_value=100000,
        value=10
    )

    keyword_filter = st.text_input(
        "Keyword filter"
    )

    op_only = st.checkbox("Only OP posts")

    remove_empty = st.checkbox(
        "Remove empty posts",
        value=True
    )

    concurrency = st.slider(
        "Concurrency",
        min_value=1,
        max_value=20,
        value=3
    )

    timeout_seconds = st.slider(
        "Request timeout",
        min_value=5,
        max_value=120,
        value=30
    )

    output_formats = st.multiselect(
        "Export formats",
        ["json", "jsonl", "csv", "txt", "sqlite"],
        default=["json"]
    )


# =====================================================
# HELPERS
# =====================================================


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()



def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return normalize_whitespace(
        soup.get_text(" ", strip=True)
    )



def extract_thread_ids_4plebs(board_name, limit):

    collected = []
    seen = set()

    page = 1

    while len(collected) < limit:

        url = f"https://archive.4plebs.org/{board_name}/page/{page}/"

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            break

        soup = BeautifulSoup(response.text, "lxml")

        links = soup.select("a[href*='/thread/']")

        if not links:
            break

        for link in links:

            href = link.get("href", "")

            match = re.search(r"/thread/(\d+)", href)

            if not match:
                continue

            tid = match.group(1)

            if tid in seen:
                continue

            seen.add(tid)
            collected.append(tid)

            if len(collected) >= limit:
                break

        page += 1

    return collected[:limit]



def extract_thread_ids_warosu(board_name, limit):

    collected = []
    seen = set()

    url = f"https://warosu.org/{board_name}/"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    if response.status_code != 200:
        return []

    soup = BeautifulSoup(response.text, "lxml")

    links = soup.select("a[href*='/thread/']")

    for link in links:

        href = link.get("href", "")

        match = re.search(r"/thread/(\d+)", href)

        if not match:
            continue

        tid = match.group(1)

        if tid in seen:
            continue

        seen.add(tid)
        collected.append(tid)

        if len(collected) >= limit:
            break

    return collected[:limit]



def build_thread_url(archive, board_name, thread_id):

    if archive == "4plebs":
        return f"https://archive.4plebs.org/{board_name}/thread/{thread_id}"

    return f"https://warosu.org/{board_name}/thread/{thread_id}"



def parse_thread(html, thread_id, board_name, archive_name):

    soup = BeautifulSoup(html, "lxml")

    posts = []

    # Different archives use different structures
    selectors = [
        "article.post",
        "div.post",
        "div.thread > div",
        "div.reply",
    ]

    articles = []

    for selector in selectors:
        found = soup.select(selector)

        if found:
            articles = found
            break

    for index, article in enumerate(articles):

        # Try multiple possible content selectors
        blockquote = (
            article.select_one("blockquote")
            or article.select_one("div.text")
            or article.select_one("div.post_comment")
            or article.select_one("div.body")
        )

        if not blockquote:
            continue

        raw_html = str(blockquote)

        content = html_to_text(raw_html)

        if not content.strip():
            continue

        author = "Anonymous"

        author_el = (
            article.select_one("span.name")
            or article.select_one("span.postername")
            or article.select_one("div.name")
        )

        if author_el:
            author = author_el.get_text(strip=True)

        timestamp = ""

        time_el = (
            article.select_one("time")
            or article.select_one("span.dateTime")
        )

        if time_el:
            timestamp = time_el.get("datetime", "") or time_el.get_text(strip=True)

        post_id = article.get("id", "")

        posts.append({
            "archive": archive_name,
            "board": board_name,
            "thread_id": thread_id,
            "post_id": post_id,
            "is_op": index == 0,
            "author": author,
            "timestamp": timestamp,
            "content": content,
            "url": build_thread_url(
                archive_name,
                board_name,
                thread_id
            )
        })

    return posts


async def fetch_thread(
    session,
    semaphore,
    archive,
    board_name,
    thread_id,
    timeout_seconds
):

    url = build_thread_url(
        archive,
        board_name,
        thread_id
    )

    async with semaphore:

        try:
            async with session.get(
                url,
                timeout=timeout_seconds
            ) as response:

                if response.status != 200:
                    return []

                html = await response.text()

                return parse_thread(
                    html,
                    thread_id,
                    board_name,
                    archive
                )

        except Exception:
            return []


async def scrape_threads(
    archive,
    board_name,
    thread_ids,
    concurrency,
    timeout_seconds
):

    semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(limit=concurrency)

    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector
    ) as session:

        tasks = [
            fetch_thread(
                session,
                semaphore,
                archive,
                board_name,
                tid,
                timeout_seconds
            )
            for tid in thread_ids
        ]

        results = await asyncio.gather(*tasks)

    posts = []

    for result in results:
        posts.extend(result)

    return posts


# =====================================================
# EXPORTS
# =====================================================


def export_json(df):
    return df.to_json(
        orient="records",
        indent=2,
        force_ascii=False
    )



def export_csv(df):
    return df.to_csv(index=False)



def export_jsonl(posts):

    lines = []

    for post in posts:
        lines.append(
            json.dumps(post, ensure_ascii=False)
        )

    return "\n".join(lines)



def export_txt(posts):

    output = []

    for post in posts:

        output.append(
            f"[{post['thread_id']}] {post['author']}"
        )

        output.append(post["content"])
        output.append("-" * 80)

    return "\n".join(output)



def export_sqlite(df, filename="threads.db"):

    conn = sqlite3.connect(filename)

    df.to_sql(
        "posts",
        conn,
        if_exists="append",
        index=False
    )

    conn.close()



def build_zip(files_dict):

    memory_file = io.BytesIO()

    with zipfile.ZipFile(
        memory_file,
        mode="w",
        compression=zipfile.ZIP_DEFLATED
    ) as zf:

        for filename, content in files_dict.items():
            zf.writestr(filename, content)

    memory_file.seek(0)

    return memory_file


# =====================================================
# MAIN
# =====================================================

if st.button("Start Crawl"):

    start_time = datetime.utcnow()

    st.info("Collecting thread IDs...")

    if archive_name == "4plebs":
        thread_ids = extract_thread_ids_4plebs(
            board,
            thread_limit
        )
    else:
        thread_ids = extract_thread_ids_warosu(
            board,
            thread_limit
        )

    st.success(f"Collected {len(thread_ids)} thread IDs")

    progress = st.progress(0)

    posts = asyncio.run(
        scrape_threads(
            archive_name,
            board,
            thread_ids,
            concurrency,
            timeout_seconds
        )
    )

    progress.progress(100)

    if remove_empty:
        posts = [
            p for p in posts
            if p["content"]
        ]

    if keyword_filter:
        posts = [
            p for p in posts
            if keyword_filter.lower()
            in p["content"].lower()
        ]

    if op_only:
        posts = [
            p for p in posts
            if p["is_op"]
        ]

    if not posts:
        st.warning("No posts collected")
        st.stop()

    df = pd.DataFrame(posts)

    st.success(f"Collected {len(df)} posts")

    elapsed = datetime.utcnow() - start_time

    st.caption(f"Finished in {elapsed}")

    # =====================================
    # STATS
    # =====================================

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Posts", len(df))

    with col2:
        st.metric(
            "Threads",
            df['thread_id'].nunique()
        )

    with col3:
        st.metric(
            "Authors",
            df['author'].nunique()
        )

    with col4:
        st.metric(
            "OP Posts",
            int(df['is_op'].sum())
        )

    st.divider()

    st.subheader("Preview")

    st.dataframe(
        df.head(1000),
        use_container_width=True
    )

    # =====================================
    # EXPORTS
    # =====================================

    export_files = {}

    if "json" in output_formats:
        export_files["threads.json"] = export_json(df)

    if "csv" in output_formats:
        export_files["threads.csv"] = export_csv(df)

    if "jsonl" in output_formats:
        export_files["threads.jsonl"] = export_jsonl(posts)

    if "txt" in output_formats:
        export_files["threads.txt"] = export_txt(posts)

    if "sqlite" in output_formats:
        sqlite_path = DATA_DIR / "threads.db"
        export_sqlite(df, sqlite_path)

        with open(sqlite_path, "rb") as f:
            export_files["threads.db"] = f.read()

    zip_buffer = build_zip(export_files)

    st.download_button(
        label="Download Export ZIP",
        data=zip_buffer,
        file_name=f"{board}_archive_export.zip",
        mime="application/zip"
    )


st.divider()

st.caption(
    "Research/educational use only. Respect archive policies and rate limits."
)
