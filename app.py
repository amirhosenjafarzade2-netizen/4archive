```python
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

MAX_SCAN_PAGES = 50000


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
    # PAGE RANGE SCRAPING
    # =========================================

    use_page_range = st.checkbox(
        "Enable page-range scraping"
    )

    start_page = st.number_input(
        "Starting page",
        min_value=1,
        value=1,
        step=1,
        disabled=not use_page_range
    )

    pages_to_scan = st.number_input(
        "Pages to scan",
        min_value=1,
        value=10,
        step=1,
        disabled=not use_page_range
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


def build_page_url(
    archive_name,
    board_name,
    page
):

    config = ARCHIVES[archive_name]

    if page == 1:

        path = config["page1"].format(
            board=board_name
        )

    else:

        path = config["pageN"].format(
            board=board_name,
            page=page
        )

    return config["base"] + path


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
# PAGE FETCHING
# =====================================================

def fetch_page_ids(
    archive_name,
    board_name,
    page
):

    url = build_page_url(
        archive_name,
        board_name,
        page
    )

    print(f"\nFETCHING PAGE: {page}")
    print(url)

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        if response.status_code != 200:

            print(
                f"BAD STATUS: "
                f"{response.status_code}"
            )

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

                tid = int(
                    match.group(1)
                )

                if tid not in ids:

                    ids.append(tid)

        print(
            f"FOUND {len(ids)} THREAD IDS"
        )

        return ids

    except Exception as e:

        print(
            f"FETCH ERROR: {e}"
        )

        return []


# =====================================================
# THREAD EXTRACTION
# =====================================================

def extract_thread_ids(
    archive_name,
    board_name,
    limit,
    start_page=1,
    pages_to_scan=None,
    progress_bar=None,
    status_text=None
):

    collected = []
    seen = set()

    if pages_to_scan is None:

        end_page = MAX_SCAN_PAGES

    else:

        end_page = (
            start_page
            + pages_to_scan
            - 1
        )

    total_pages = (
        end_page
        - start_page
        + 1
    )

    scanned = 0

    for page in range(
        start_page,
        end_page + 1
    ):

        scanned += 1

        if progress_bar:

            progress = int(
                (scanned / total_pages)
                * 100
            )

            progress_bar.progress(
                min(progress, 100)
            )

        if status_text:

            status_text.caption(
                f"Scanning page {page} | "
                f"Collected {len(collected)} threads"
            )

        page_thread_ids = fetch_page_ids(
            archive_name,
            board_name,
            page
        )

        if not page_thread_ids:

            print(
                f"EMPTY PAGE: {page}"
            )

            continue

        print(
            f"PAGE {page} -> "
            f"{len(page_thread_ids)} THREADS"
        )

        for numeric_thread_id in page_thread_ids:

            thread_id = str(
                numeric_thread_id
            )

            if thread_id in seen:
                continue

            seen.add(thread_id)

            collected.append(
                thread_id
            )

            print(
                f"FOUND THREAD: "
                f"{thread_id}"
            )

            if len(collected) >= limit:

                print(
                    "LIMIT REACHED"
                )

                return collected[:limit]

    return collected[:limit]


# =====================================================
# THREAD FILTER HELPERS
# =====================================================

def extract_thread_subject(soup):

    subject_selectors = [
        ".subject",
        ".post_title",
        ".title",
        ".thread_title"
    ]

    for selector in subject_selectors:

        el = soup.select_one(selector)

        if el:

            text = normalize_whitespace(
                el.get_text(
                    " ",
                    strip=True
                )
            )

            if text:
                return text

    return ""


def extract_post_elements(soup):

    selectors = [
        ".post",
        ".thread-post",
        "article.post",
        ".postContainer"
    ]

    for selector in selectors:

        posts = soup.select(selector)

        if posts:
            return posts

    return []


def extract_post_text(post):

    text_selectors = [
        "blockquote",
        ".text",
        ".post_message",
        ".body",
        ".message"
    ]

    for selector in text_selectors:

        el = post.select_one(selector)

        if el:

            text = normalize_whitespace(
                el.get_text(
                    " ",
                    strip=True
                )
            )

            if text:
                return text

    text = normalize_whitespace(
        post.get_text(
            " ",
            strip=True
        )
    )

    return text


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

    thread_subject = extract_thread_subject(
        soup
    )

    post_elements = extract_post_elements(
        soup
    )

    if not post_elements:

        return []

    for idx, post in enumerate(post_elements):

        content = extract_post_text(
            post
        )

        if len(content) < 5:
            continue

        post_id = (
            post.get("id")
            or f"{thread_id}_{idx}"
        )

        posts.append({

            "archive":
                archive_name,

            "board":
                board_name,

            "thread_id":
                thread_id,

            "post_id":
                str(post_id),

            "is_op":
                idx == 0,

            "thread_subject":
                thread_subject,

            "author":
                "Anonymous",

            "timestamp":
                "",

            "content":
                content,

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
    timeout_seconds,
    thread_keyword_filter=None
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

                # =====================================
                # EARLY FILTER
                # =====================================

                if thread_keyword_filter:

                    soup = BeautifulSoup(
                        html,
                        "html.parser"
                    )

                    keyword = (
                        thread_keyword_filter
                        .lower()
                        .strip()
                    )

                    thread_subject = (
                        extract_thread_subject(
                            soup
                        ).lower()
                    )

                    post_elements = (
                        extract_post_elements(
                            soup
                        )
                    )

                    op_text = ""

                    if post_elements:

                        op_text = (
                            extract_post_text(
                                post_elements[0]
                            ).lower()
                        )

                    subject_match = (
                        keyword in thread_subject
                    )

                    op_match = (
                        keyword in op_text
                    )

                    # SKIP THREAD COMPLETELY
                    if not (
                        subject_match
                        or
                        op_match
                    ):

                        print(
                            f"SKIPPED THREAD "
                            f"{thread_id}"
                        )

                        return []

                # =====================================
                # PARSE ONLY MATCHING THREADS
                # =====================================

                parsed = parse_thread(
                    html,
                    thread_id,
                    board_name,
                    archive_name
                )

                return parsed

        except Exception as e:

            print(
                f"THREAD FETCH ERROR: "
                f"{thread_id} -> {e}"
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
    timeout_seconds,
    thread_keyword_filter=None
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
                timeout_seconds,
                thread_keyword_filter
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

    # =========================================
    # EXTRACT IDS
    # =========================================

    thread_ids = extract_thread_ids(

        archive_source,
        board,
        thread_limit,

        start_page=(
            int(start_page)
            if use_page_range
            else 1
        ),

        pages_to_scan=(
            int(pages_to_scan)
            if use_page_range
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

    # =========================================
    # SCRAPE THREADS
    # =========================================

    scraping_bar = st.progress(0)

    scraping_status = st.empty()

    scraping_status.caption(
        "Scraping matching threads..."
    )

    posts = asyncio.run(

        scrape_threads(
            archive_source,
            board,
            thread_ids,
            concurrency,
            timeout_seconds,
            thread_keyword_filter
        )

    )

    scraping_bar.progress(100)

    scraping_status.caption(
        "Thread scraping complete."
    )

    # =========================================
    # POST FILTER
    # =========================================

    if post_keyword_filter:

        keyword = (
            post_keyword_filter
            .lower()
            .strip()
        )

        posts = [

            p for p in posts

            if keyword
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
```
