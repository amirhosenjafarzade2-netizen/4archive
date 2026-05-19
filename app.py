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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

DATA_DIR = Path("exports")
DATA_DIR.mkdir(exist_ok=True)


# =====================================================
# ARCHIVES
# =====================================================

ARCHIVES = {

    # =========================================
    # WAROSU
    # =========================================

    "warosu": {

        "base":
            "https://warosu.org",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/?task=page&page={page}",

        "thread":
            "/{board}/thread/{thread_id}",
    },

    # =========================================
    # 4PLEBS
    # =========================================

    "4plebs": {

        "base":
            "https://archive.4plebs.org",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/page/{page}/",

        "thread":
            "/{board}/thread/{thread_id}/",
    },

    # =========================================
    # DESUARCHIVE
    # =========================================

    "desuarchive": {

        "base":
            "https://desuarchive.org",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/page/{page}/",

        # IMPORTANT:
        # no /{board}/ here
        "thread":
            "/thread/{thread_id}/",
    },

    # =========================================
    # B4K
    # =========================================

    "b4k": {

        "base":
            "https://arch.b4k.dev",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/page/{page}/",

        # IMPORTANT:
        # no /{board}/ here
        "thread":
            "/thread/{thread_id}/",
    }
}


# =====================================================
# PAGE
# =====================================================

st.set_page_config(
    page_title="4chan Archive Crawler",
    layout="wide"
)

st.title("4chan Archive Crawler")

st.caption(
    "Supports Warosu, 4plebs, "
    "Desuarchive and b4k"
)


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    archive_source = st.selectbox(
        "Archive Source",
        [
            "warosu",
            "4plebs",
            "desuarchive",
            "b4k"
        ]
    )

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
        max_value=50,
        value=5
    )

    timeout_seconds = st.slider(
        "Timeout",
        min_value=5,
        max_value=120,
        value=30
    )

    output_formats = st.multiselect(
        "Export formats",
        [
            "json",
            "jsonl",
            "csv",
            "txt",
            "sqlite"
        ],
        default=["json"]
    )


# =====================================================
# HELPERS
# =====================================================

def normalize_whitespace(text):

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# =====================================================
# URL BUILDER
# =====================================================

def build_thread_url(
    archive_name,
    board_name,
    thread_id
):

    config = ARCHIVES[archive_name]

    path = config["thread"].format(
        board=board_name,
        thread_id=thread_id
    )

    return (
        config["base"] + path
    )


# =====================================================
# THREAD EXTRACTION
# =====================================================

def extract_thread_ids(
    archive_name,
    board_name,
    limit
):

    config = ARCHIVES[archive_name]

    collected = []
    seen = set()

    page = 1

    while len(collected) < limit:

        # -----------------------------------------
        # BUILD PAGE URL
        # -----------------------------------------

        if page == 1:

            path = config["page1"].format(
                board=board_name
            )

        else:

            path = config["pageN"].format(
                board=board_name,
                page=page
            )

        url = config["base"] + path

        print(f"\nFETCHING PAGE: {url}")

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            if response.status_code != 200:

                print(
                    "BAD STATUS:",
                    response.status_code
                )

                break

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            found_any = False

            links = soup.find_all(
                "a",
                href=True
            )

            for link in links:

                href = link["href"]

                # =====================================
                # WORKS FOR:
                #
                # /g/thread/123456/
                # /a/thread/123456/#p123
                # https://desuarchive.org/g/thread/123456/
                # https://arch.b4k.dev/v/thread/123456/
                # =====================================

                match = re.search(
                    r"/[a-zA-Z0-9]+/thread/(\d+)",
                    href
                )

                if not match:
                    continue

                thread_id = match.group(1)

                if thread_id in seen:
                    continue

                seen.add(thread_id)

                collected.append(thread_id)

                found_any = True

                print(
                    f"FOUND THREAD: {thread_id}"
                )

                if len(collected) >= limit:
                    break

            if not found_any:

                print(
                    "NO THREADS FOUND"
                )

                break

            page += 1

        except Exception as e:

            print(
                "EXTRACTION ERROR:",
                e
            )

            break

    return collected[:limit]


# =====================================================
# PARSER
# =====================================================

def parse_thread(
    html,
    thread_id,
    board_name,
    archive_name
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    posts = []

    seen = set()

    candidates = []

    # =========================================
    # DESUARCHIVE / B4K
    # =========================================

    candidates.extend(
        soup.select(".post_data")
    )

    candidates.extend(
        soup.select(".text")
    )

    # =========================================
    # WAROSU / 4PLEBS
    # =========================================

    candidates.extend(
        soup.find_all("blockquote")
    )

    candidates.extend(
        soup.find_all("article")
    )

    for idx, block in enumerate(candidates):

        text = normalize_whitespace(
            block.get_text(
                " ",
                strip=True
            )
        )

        if len(text) < 20:
            continue

        key = text[:500]

        if key in seen:
            continue

        seen.add(key)

        posts.append({

            "archive":
                archive_name,

            "board":
                board_name,

            "thread_id":
                thread_id,

            "post_id":
                f"{thread_id}_{idx}",

            "is_op":
                idx == 0,

            "author":
                "Anonymous",

            "timestamp":
                "",

            "content":
                text,

            "url":
                build_thread_url(
                    archive_name,
                    board_name,
                    thread_id
                )
        })

    return posts


# =====================================================
# FETCH THREAD
# =====================================================

async def fetch_thread(
    session,
    semaphore,
    archive_name,
    board_name,
    thread_id,
    timeout_seconds
):

    url = build_thread_url(
        archive_name,
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

                    print(
                        f"BAD THREAD STATUS: "
                        f"{response.status}"
                    )

                    return []

                html = await response.text()

                parsed = parse_thread(
                    html,
                    thread_id,
                    board_name,
                    archive_name
                )

                print(
                    f"THREAD {thread_id}: "
                    f"{len(parsed)} posts"
                )

                return parsed

        except Exception as e:

            print(
                "THREAD ERROR:",
                e
            )

            return []


# =====================================================
# SCRAPER
# =====================================================

async def scrape_threads(
    archive_name,
    board_name,
    thread_ids,
    concurrency,
    timeout_seconds
):

    semaphore = asyncio.Semaphore(
        concurrency
    )

    connector = aiohttp.TCPConnector(
        limit=concurrency,
        ssl=False
    )

    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector
    ) as session:

        tasks = [

            fetch_thread(
                session,
                semaphore,
                archive_name,
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

    return df.to_csv(
        index=False
    )


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
            f"[THREAD {post['thread_id']}]"
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
        if_exists="replace",
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

    thread_ids = extract_thread_ids(
        archive_source,
        board,
        thread_limit
    )

    st.success(
        f"Collected "
        f"{len(thread_ids)} thread IDs"
    )

    if not thread_ids:

        st.error(
            "No thread IDs found."
        )

        st.stop()

    progress = st.progress(0)

    posts = asyncio.run(

        scrape_threads(
            archive_source,
            board,
            thread_ids,
            concurrency,
            timeout_seconds
        )

    )

    progress.progress(100)

    # =========================================
    # FILTERS
    # =========================================

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
            "No posts collected."
        )

        st.stop()

    # =========================================
    # DATAFRAME
    # =========================================

    df = pd.DataFrame(posts)

    elapsed = (
        datetime.now() - start_time
    )

    st.success(
        f"Collected {len(df)} posts"
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
            "Archives",
            df["archive"].nunique()
        )

    st.divider()

    st.subheader("Preview")

    st.dataframe(
        df.head(1000),
        use_container_width=True
    )

    # =========================================
    # EXPORT FILES
    # =========================================

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
            f"{archive_source}_{board}_archive.zip"
        ),
        mime="application/zip"
    )


st.divider()

st.caption(
    "Research / educational use only."
)
