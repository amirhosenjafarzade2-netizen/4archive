import asyncio
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

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
            "/{board}/gallery/1/",

        "pageN":
            "/{board}/gallery/{page}/",

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
    # SEARCH MODE (replaces keyword filter)
    # =========================================

    search_mode = st.selectbox(
        "Thread Search Mode",
        [
            "Disabled",
            "Subject",
            "OP Text"
        ]
    )

    search_keyword = st.text_input(
        "Search Keyword",
        disabled=(search_mode == "Disabled")
    )

    # =========================================
    # PAGE RANGE
    # =========================================

    use_page_range = st.checkbox(
        "Enable page-range",
        help=(
            "Limit which pages of results to scan. "
            "Works with both Search Mode and page scraping."
        )
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
# ARCHIVE-NATIVE SEARCH URL BUILDER
# =====================================================

def build_search_url(
    archive_name,
    board_name,
    keyword,
    mode,
    page=1
):
    """
    Build an archive-specific search URL.

    mode: "Subject" | "OP Text"
    page: 1-based page number
    """

    kw = quote(keyword)

    # -------------------------------------------------
    # WAROSU: query-string based search
    # -------------------------------------------------

    if archive_name == "warosu":

        if mode == "Subject":

            url = (
                f"https://warosu.org/{board_name}/"
                f"?task=search2"
                f"&ghost=false"
                f"&search_text="
                f"&search_subject={kw}"
                f"&search_username="
                f"&search_tripcode="
                f"&search_email="
                f"&search_filename="
                f"&search_datefrom="
                f"&search_dateto="
                f"&search_media_hash="
                f"&search_op=all"
                f"&search_del=dontcare"
                f"&search_int=dontcare"
                f"&search_ord=new"
                f"&search_capcode=all"
                f"&search_res=post"
            )

        else:  # OP Text

            url = (
                f"https://warosu.org/{board_name}/"
                f"?task=search2"
                f"&ghost=false"
                f"&search_text={kw}"
                f"&search_subject="
                f"&search_username="
                f"&search_tripcode="
                f"&search_email="
                f"&search_filename="
                f"&search_datefrom="
                f"&search_dateto="
                f"&search_media_hash="
                f"&search_op=op"
                f"&search_del=dontcare"
                f"&search_int=dontcare"
                f"&search_ord=new"
                f"&search_capcode=all"
                f"&search_res=post"
            )

        if page > 1:
            url += f"&offset={page}"

        return url

    # -------------------------------------------------
    # 4PLEBS / DESUARCHIVE / B4K: path-based search
    # -------------------------------------------------

    base = ARCHIVES[archive_name]["base"]

    if mode == "Subject":

        if page == 1:
            return (
                f"{base}/{board_name}/"
                f"search/subject/{kw}/"
            )

        return (
            f"{base}/{board_name}/"
            f"search/subject/{kw}/"
            f"page/{page}/"
        )

    else:  # OP Text

        if page == 1:
            return (
                f"{base}/{board_name}/"
                f"search/text/{kw}/type/op/"
            )

        return (
            f"{base}/{board_name}/"
            f"search/text/{kw}/type/op/"
            f"page/{page}/"
        )


# =====================================================
# ARCHIVE-NATIVE SEARCH
# =====================================================

def search_thread_ids(
    archive_name,
    board_name,
    keyword,
    mode,
    limit,
    start_page=1,
    pages_to_scan=None,
    status_text=None
):
    """
    Query the archive's own search endpoint and
    collect up to `limit` matching thread IDs.

    start_page / pages_to_scan optionally restrict
    which pages of search results to read.
    """

    collected = []
    seen = set()
    page = start_page

    end_page = (
        (start_page + pages_to_scan - 1)
        if pages_to_scan is not None
        else None
    )

    while len(collected) < limit and (
        end_page is None or page <= end_page
    ):

        url = build_search_url(
            archive_name,
            board_name,
            keyword,
            mode,
            page
        )

        print(f"\nSEARCH PAGE {page}")
        print(url)

        if status_text:
            status_text.caption(
                f"Searching page {page} | "
                f"Found {len(collected)} threads so far…"
            )

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

                break

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            found_this_page = 0

            # ------------------------------------------
            # Try article-based extraction first
            # (4plebs, desuarchive, b4k)
            # ------------------------------------------

            articles = soup.select("article.post")

            if articles:

                for article in articles:

                    link = article.select_one(
                        'a[href*="/thread/"]'
                    )

                    if not link:
                        continue

                    m = re.search(
                        r"/thread/(\d+)",
                        link["href"]
                    )

                    if not m:
                        continue

                    tid = m.group(1)

                    if tid in seen:
                        continue

                    seen.add(tid)
                    collected.append(tid)
                    found_this_page += 1

                    print(f"FOUND THREAD: {tid}")

                    if len(collected) >= limit:
                        return collected

            else:

                # --------------------------------------
                # Fallback: scan all links for /thread/
                # (warosu and other archives)
                # --------------------------------------

                links = soup.find_all(
                    "a",
                    href=True
                )

                for link in links:

                    href = link["href"]

                    m = re.search(
                        r"/thread/(\d+)",
                        href
                    )

                    if not m:
                        continue

                    tid = m.group(1)

                    if tid in seen:
                        continue

                    seen.add(tid)
                    collected.append(tid)
                    found_this_page += 1

                    print(f"FOUND THREAD: {tid}")

                    if len(collected) >= limit:
                        return collected

            print(
                f"FOUND {found_this_page} "
                f"THREADS ON PAGE {page}"
            )

            # ------------------------------------------
            # Stop if nothing came back on this page
            # ------------------------------------------

            if found_this_page == 0:

                print("NO MORE RESULTS")
                break

            page += 1

        except Exception as e:

            print(f"SEARCH ERROR: {e}")
            break

    return collected


# =====================================================
# PAGE FETCHING  (used when search is Disabled)
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

            # ---------------------------------
            # 4PLEBS SPECIAL HANDLING ONLY
            # ---------------------------------

            if archive_name == "4plebs":

                patterns = [

                    r"/thread/(\d+)",
                    r"/[a-zA-Z0-9]+/thread/(\d+)",
                    r"thread/(\d+)",

                ]

                thread_id = None

                for pattern in patterns:

                    match = re.search(
                        pattern,
                        href
                    )

                    if match:

                        thread_id = match.group(1)
                        break

                if thread_id:

                    tid = int(thread_id)

                    if tid not in ids:

                        ids.append(tid)

            else:

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
# THREAD ID EXTRACTION
# (search-first, page-scan fallback)
# =====================================================

def extract_thread_ids(
    archive_name,
    board_name,
    limit,
    search_kw=None,
    search_md=None,
    start_page=1,
    pages_to_scan=None,
    progress_bar=None,
    status_text=None
):
    """
    If a search keyword + mode are provided, use the
    archive's native search endpoint — much faster.

    Otherwise fall back to page-scanning.
    """

    # =========================================
    # FAST PATH: archive-native search
    # =========================================

    if search_kw and search_md and search_md != "Disabled":

        print(
            f"USING ARCHIVE SEARCH: "
            f'"{search_kw}" ({search_md})'
        )

        return search_thread_ids(
            archive_name,
            board_name,
            search_kw,
            search_md,
            limit,
            start_page=start_page,
            pages_to_scan=pages_to_scan,
            status_text=status_text
        )

    # =========================================
    # SLOW PATH: page-by-page scan
    # =========================================

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

    # =====================================
    # POST CONTAINERS
    # =====================================

    containers = []

    selectors = [
        ".post",
        ".reply",
        "article"
    ]

    for selector in selectors:

        found = soup.select(selector)

        if found:

            containers = found
            break

    # fallback

    if not containers:

        containers = soup.find_all(
            "blockquote"
        )

    seen = set()

    for idx, container in enumerate(containers):

        text = normalize_whitespace(
            container.get_text(
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

        post_id = (
            container.get("id")
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
                post_id,

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

    # Validate: if search is enabled, keyword must be set
    if search_mode != "Disabled" and not search_keyword.strip():

        st.error(
            "Please enter a Search Keyword "
            "or set Thread Search Mode to Disabled."
        )

        st.stop()

    start_time = datetime.now()

    collecting_placeholder = st.empty()

    # Different message depending on mode
    if search_mode != "Disabled":

        collecting_placeholder.info(
            f"Searching archive for "
            f'"{search_keyword}" ({search_mode})…'
        )

    else:

        collecting_placeholder.info(
            "Collecting thread IDs…"
        )

    loading_bar = st.progress(0)

    loading_status = st.empty()

    # =========================================
    # EXTRACT / SEARCH IDS
    # =========================================

    thread_ids = extract_thread_ids(

        archive_source,
        board,
        thread_limit,

        search_kw=(
            search_keyword.strip()
            if search_mode != "Disabled"
            else None
        ),

        search_md=(
            search_mode
            if search_mode != "Disabled"
            else None
        ),

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
            "No matching thread IDs found."
        )

        st.stop()

    # =========================================
    # SCRAPE THREADS
    # =========================================

    scraping_bar = st.progress(0)

    scraping_status = st.empty()

    scraping_status.caption(
        "Scraping thread contents…"
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
    # POST FILTER
    # =========================================

    if post_keyword_filter:

        keyword = (
            post_keyword_filter.lower()
        )

        posts = [

            p for p in posts

            if keyword in
            p["content"].lower()
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
