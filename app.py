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

    "desuarchive": {

        "base":
            "https://desuarchive.org",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/page/{page}/",

        "thread":
            "/thread/{thread_id}/",
    },

    "b4k": {

        "base":
            "https://arch.b4k.dev",

        "page1":
            "/{board}/",

        "pageN":
            "/{board}/page/{page}/",

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

    # =========================================
    # RANGE SCRAPING
    # =========================================

    use_range_scrape = st.checkbox(
        "Enable thread ID range scraping"
    )

    range_start = st.number_input(
        "Start thread ID",
        min_value=0,
        value=1000,
        step=1,
        disabled=not use_range_scrape
    )

    range_end = st.number_input(
        "End thread ID",
        min_value=0,
        value=2000,
        step=1,
        disabled=not use_range_scrape
    )

    # =========================================
    # FILTERS
    # =========================================

    thread_keyword_filter = st.text_input(
        "Thread subject / OP keyword filter"
    )

    post_keyword_filter = st.text_input(
        "Post content keyword filter"
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
# PAGE FETCHER
# =====================================================

def fetch_archive_page(
    archive_name,
    board_name,
    page
):

    config = ARCHIVES[archive_name]

    try:

        if page <= 1:

            path = config["page1"].format(
                board=board_name
            )

        else:

            path = config["pageN"].format(
                board=board_name,
                page=page
            )

        url = config["base"] + path

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        ids = []

        links = soup.find_all(
            "a",
            href=True
        )

        for link in links:

            href = link["href"]

            match = re.search(
                r"/[a-zA-Z0-9]+/thread/(\d+)",
                href
            )

            if match:

                tid = int(match.group(1))

                if tid not in ids:
                    ids.append(tid)

        return ids

    except Exception as e:

        print(
            "PAGE FETCH ERROR:",
            e
        )

        return []


# =====================================================
# THREAD EXTRACTION
# =====================================================

def extract_thread_ids(
    archive_name,
    board_name,
    limit,
    range_start=None,
    range_end=None,
    progress_bar=None,
    status_text=None
):

    collected = []
    seen = set()

    MAX_PAGES = 100000
    EMPTY_PAGE_LIMIT = 25

    # =========================================
    # NORMAL MODE
    # =========================================

    if range_start is None or range_end is None:

        page = 1
        empty_pages = 0

        while len(collected) < limit:

            ids = fetch_archive_page(
                archive_name,
                board_name,
                page
            )

            if not ids:

                empty_pages += 1

                if empty_pages >= EMPTY_PAGE_LIMIT:
                    break

                page += 1
                continue

            empty_pages = 0

            for tid in ids:

                if tid in seen:
                    continue

                seen.add(tid)

                collected.append(
                    str(tid)
                )

                if len(collected) >= limit:
                    break

            if progress_bar:

                progress = min(
                    95,
                    int(
                        (
                            len(collected)
                            / limit
                        ) * 100
                    )
                )

                progress_bar.progress(
                    progress
                )

            if status_text:

                status_text.caption(
                    f"Scanning page {page} | "
                    f"Collected "
                    f"{len(collected)} threads"
                )

            page += 1

        return collected[:limit]

    # =========================================
    # RANGE MODE
    # =========================================

    low = 1
    high = MAX_PAGES

    best_page = 1

    # =========================================
    # BINARY SEARCH
    # =========================================

    while low <= high:

        mid = (low + high) // 2

        ids = fetch_archive_page(
            archive_name,
            board_name,
            mid
        )

        if not ids:

            high = mid - 1
            continue

        highest = max(ids)
        lowest = min(ids)

        print(
            f"PAGE {mid} => "
            f"{highest} -> {lowest}"
        )

        if highest < range_start:

            high = mid - 1

        elif lowest > range_end:

            low = mid + 1

        else:

            best_page = mid
            break

    # =========================================
    # LOCAL SCAN
    # =========================================

    page = max(1, best_page - 3)

    scanned_pages = 0
    empty_pages = 0

    while len(collected) < limit:

        scanned_pages += 1

        if scanned_pages > MAX_PAGES:
            break

        ids = fetch_archive_page(
            archive_name,
            board_name,
            page
        )

        if not ids:

            empty_pages += 1

            if empty_pages >= EMPTY_PAGE_LIMIT:
                break

            page += 1
            continue

        empty_pages = 0

        highest = max(ids)
        lowest = min(ids)

        print(
            f"SCAN PAGE {page} => "
            f"{highest} -> {lowest}"
        )

        # =====================================
        # STOP CONDITIONS
        # =====================================

        if highest < range_start:
            break

        if lowest > range_end:

            page += 1
            continue

        # =====================================
        # COLLECT THREADS
        # =====================================

        for tid in ids:

            if tid in seen:
                continue

            if range_start <= tid <= range_end:

                seen.add(tid)

                collected.append(
                    str(tid)
                )

                print(
                    f"FOUND THREAD {tid}"
                )

                if len(collected) >= limit:
                    break

        if progress_bar:

            progress = min(
                95,
                int(
                    (
                        len(collected)
                        / limit
                    ) * 100
                )
            )

            progress_bar.progress(
                progress
            )

        if status_text:

            status_text.caption(
                f"Scanning page {page} | "
                f"Collected "
                f"{len(collected)} threads"
            )

        page += 1

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

    candidates.extend(
        soup.select(".post_data")
    )

    candidates.extend(
        soup.select(".text")
    )

    candidates.extend(
        soup.find_all("blockquote")
    )

    candidates.extend(
        soup.find_all("article")
    )

    thread_subject = ""

    subject_selectors = [
        ".subject",
        ".post_title",
        ".title",
        ".thread_title"
    ]

    for selector in subject_selectors:

        el = soup.select_one(selector)

        if el:

            thread_subject = normalize_whitespace(
                el.get_text(
                    " ",
                    strip=True
                )
            )

            if thread_subject:
                break

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

        is_op = idx == 0

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
                is_op,

            "thread_subject":
                thread_subject,

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
                    return []

                html = await response.text()

                return parse_thread(
                    html,
                    thread_id,
                    board_name,
                    archive_name
                )

        except Exception:

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
            f"SUBJECT: "
            f"{post.get('thread_subject', '')}"
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

    collecting_placeholder = st.empty()

    collecting_placeholder.info(
        "Collecting thread IDs..."
    )

    loading_bar = st.progress(0)

    loading_status = st.empty()

    thread_ids = extract_thread_ids(

        archive_source,
        board,
        thread_limit,

        range_start=(
            int(range_start)
            if use_range_scrape
            else None
        ),

        range_end=(
            int(range_end)
            if use_range_scrape
            else None
        ),

        progress_bar=loading_bar,
        status_text=loading_status
    )

    loading_bar.progress(100)

    loading_status.caption(
        "Thread collection complete."
    )

    collecting_placeholder.success(
        f"Collected "
        f"{len(thread_ids)} thread IDs"
    )

    if not thread_ids:

        st.error(
            "No thread IDs found."
        )

        st.stop()

    scraping_bar = st.progress(0)

    scraping_status = st.empty()

    scraping_status.caption(
        "Scraping thread contents..."
    )

    posts = asyncio.run(

        scrape_threads(
            archive_source,
            board,
            thread_ids,
            concurrency,
            timeout_seconds
        )

    )

    scraping_bar.progress(100)

    scraping_status.caption(
        "Thread scraping complete."
    )

    # =========================================
    # THREAD FILTER
    # =========================================

    if thread_keyword_filter:

        keyword = (
            thread_keyword_filter.lower()
        )

        matching_thread_ids = set()

        for p in posts:

            subject_match = (
                keyword in
                p.get(
                    "thread_subject",
                    ""
                ).lower()
            )

            op_match = (
                p["is_op"]
                and
                keyword in
                p["content"].lower()
            )

            if subject_match or op_match:

                matching_thread_ids.add(
                    p["thread_id"]
                )

        posts = [

            p for p in posts

            if p["thread_id"]
            in matching_thread_ids
        ]

    # =========================================
    # POST FILTER
    # =========================================

    if post_keyword_filter:

        posts = [

            p for p in posts

            if post_keyword_filter.lower()
            in p["content"].lower()
        ]

    # =========================================
    # OP ONLY
    # =========================================

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
    # EXPORTS
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
