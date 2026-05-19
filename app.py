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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    )
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
st.caption("Bulk downloader for Warosu archives")


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    board = st.text_input(
        "Board",
        value="biz"
    )

    thread_limit = st.number_input(
        "Threads to fetch",
        min_value=1,
        max_value=100000,
        value=100
    )

    keyword_filter = st.text_input(
        "Keyword filter"
    )

    op_only = st.checkbox(
        "Only OP posts"
    )

    concurrency = st.slider(
        "Concurrency",
        min_value=1,
        max_value=20,
        value=3
    )

    timeout_seconds = st.slider(
        "Timeout",
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


# =====================================================
# THREAD ID EXTRACTION
# =====================================================

def extract_thread_ids_warosu(board_name, limit):

    collected = []
    seen = set()

    page = 1

    while len(collected) < limit:

        if page == 1:
            url = f"https://warosu.org/{board_name}/"
        else:
            url = (
                f"https://warosu.org/{board_name}/"
                f"?task=page&page={page}"
            )

        print(f"FETCHING PAGE: {url}")

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            if response.status_code != 200:
                print("BAD STATUS:", response.status_code)
                break

            soup = BeautifulSoup(
                response.text,
                "lxml"
            )

            links = soup.find_all(
                "a",
                href=True
            )

            found_any = False

            for link in links:

                href = link["href"]

                match = re.search(
                    r"/thread/(\d+)",
                    href
                )

                if not match:
                    continue

                thread_id = match.group(1)

                if thread_id in seen:
                    continue

                found_any = True

                seen.add(thread_id)
                collected.append(thread_id)

                if len(collected) >= limit:
                    break

            if not found_any:
                print("NO THREADS FOUND ON PAGE")
                break

            page += 1

        except Exception as e:
            print("ERROR:", e)
            break

    return collected[:limit]


# =====================================================
# THREAD URL
# =====================================================

def build_thread_url(board_name, thread_id):

    return (
        f"https://warosu.org/"
        f"{board_name}/thread/{thread_id}"
    )


# =====================================================
# PARSER
# =====================================================

def parse_thread(
    html,
    thread_id,
    board_name
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    posts = []

    blocks = soup.find_all(
        ["blockquote", "article", "div"]
    )

    for idx, block in enumerate(blocks):

        text = normalize_whitespace(
            block.get_text(
                " ",
                strip=True
            )
        )

        if len(text) < 20:
            continue

        posts.append({
            "board": board_name,
            "thread_id": thread_id,
            "post_id": f"{thread_id}_{idx}",
            "is_op": idx == 0,
            "author": "Anonymous",
            "timestamp": "",
            "content": text,
            "url": build_thread_url(
                board_name,
                thread_id
            )
        })

    # dedupe
    unique = []
    seen = set()

    for post in posts:

        key = post["content"][:300]

        if key in seen:
            continue

        seen.add(key)
        unique.append(post)

    return unique


# =====================================================
# ASYNC FETCH
# =====================================================

async def fetch_thread(
    session,
    semaphore,
    board_name,
    thread_id,
    timeout_seconds
):

    url = build_thread_url(
        board_name,
        thread_id
    )

    async with semaphore:

        try:

            async with session.get(
                url,
                timeout=timeout_seconds,
                ssl=False
            ) as response:

                if response.status != 200:
                    print("BAD THREAD STATUS:", response.status)
                    return []

                html = await response.text()

                parsed = parse_thread(
                    html,
                    thread_id,
                    board_name
                )

                print(
                    f"THREAD {thread_id}: "
                    f"{len(parsed)} posts"
                )

                return parsed

        except Exception as e:

            print("THREAD ERROR:", e)

            return []


# =====================================================
# SCRAPER
# =====================================================

async def scrape_threads(
    board_name,
    thread_ids,
    concurrency,
    timeout_seconds
):

    semaphore = asyncio.Semaphore(
        concurrency
    )

    connector = aiohttp.TCPConnector(
        limit=concurrency
    )

    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector
    ) as session:

        tasks = [

            fetch_thread(
                session,
                semaphore,
                board_name,
                thread_id,
                timeout_seconds
            )

            for thread_id in thread_ids
        ]

        results = await asyncio.gather(
            *tasks
        )

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
            json.dumps(
                post,
                ensure_ascii=False
            )
        )

    return "\n".join(lines)


def export_txt(posts):

    output = []

    for post in posts:

        output.append(
            f"[{post['thread_id']}]"
        )

        output.append(
            post["content"]
        )

        output.append(
            "-" * 80
        )

    return "\n".join(output)


def export_sqlite(
    df,
    filename="threads.db"
):

    conn = sqlite3.connect(
        filename
    )

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

            zf.writestr(
                filename,
                content
            )

    memory_file.seek(0)

    return memory_file


# =====================================================
# MAIN
# =====================================================

if st.button("Start Crawl"):

    start_time = datetime.now()

    st.info(
        "Collecting thread IDs..."
    )

    thread_ids = extract_thread_ids_warosu(
        board,
        thread_limit
    )

    st.success(
        f"Collected "
        f"{len(thread_ids)} thread IDs"
    )

    if not thread_ids:

        st.error(
            "No thread IDs found"
        )

        st.stop()

    progress = st.progress(0)

    posts = asyncio.run(

        scrape_threads(
            board,
            thread_ids,
            concurrency,
            timeout_seconds
        )

    )

    progress.progress(100)

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

        st.error(
            "No posts collected"
        )

        st.stop()

    df = pd.DataFrame(posts)

    elapsed = (
        datetime.now() - start_time
    )

    st.success(
        f"Collected "
        f"{len(df)} posts"
    )

    st.caption(
        f"Finished in {elapsed}"
    )

    st.divider()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Posts",
            len(df)
        )

    with col2:
        st.metric(
            "Threads",
            df["thread_id"].nunique()
        )

    with col3:
        st.metric(
            "Authors",
            df["author"].nunique()
        )

    st.divider()

    st.subheader("Preview")

    st.dataframe(
        df.head(1000),
        use_container_width=True
    )

    export_files = {}

    if "json" in output_formats:

        export_files[
            "threads.json"
        ] = export_json(df)

    if "csv" in output_formats:

        export_files[
            "threads.csv"
        ] = export_csv(df)

    if "jsonl" in output_formats:

        export_files[
            "threads.jsonl"
        ] = export_jsonl(posts)

    if "txt" in output_formats:

        export_files[
            "threads.txt"
        ] = export_txt(posts)

    if "sqlite" in output_formats:

        sqlite_path = (
            DATA_DIR / "threads.db"
        )

        export_sqlite(
            df,
            sqlite_path
        )

        with open(
            sqlite_path,
            "rb"
        ) as f:

            export_files[
                "threads.db"
            ] = f.read()

    zip_buffer = build_zip(
        export_files
    )

    st.download_button(
        label="Download ZIP",
        data=zip_buffer,
        file_name=(
            f"{board}_archive.zip"
        ),
        mime="application/zip"
    )


st.divider()

st.caption(
    "Research/educational use only."
)
```
